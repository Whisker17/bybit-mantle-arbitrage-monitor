#!/usr/bin/env python3
"""WHI-767: chain-backfill MM attribution analysis (re-runnable).

Fetches AMM swaps + LOP fills + ERC-20 transfers from Mantle, joins Bybit
1m klines for convergence, builds per-address inventory ledgers, scores
draft labels, and writes:

  docs/references/mm-attribution-analysis.md
  data/mm_analysis/  (cache; gitignored)

Usage (repo root, needs MANTLE_RPC_URL or public fallback):

  uv run python scripts/mm_attribution_analysis.py
  uv run python scripts/mm_attribution_analysis.py --days 30 --skip-fetch
  uv run python scripts/mm_attribution_analysis.py --report-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml
from eth_utils import keccak  # type: ignore[attr-defined]

# Allow `uv run python scripts/...` without installing editable path quirks.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from monitor.attribution.config import load_attribution_config  # noqa: E402
from monitor.attribution.convergence import (  # noqa: E402
    bybit_move_aligned,
    is_converging,
    resolve_bybit_mid_prev,
)
from monitor.attribution.mm_draft import (  # noqa: E402
    DraftThresholds,
    InventoryEvent,
    LedgerKind,
    TransferEdge,
    aggregate_address_features,
    assign_draft_label,
    cluster_cex_candidates,
    count_cex_touches,
    decode_rfq_fill_from_receipt,
)
from monitor.collector.config import load_dotenv, resolve_mantle_rpc_url  # noqa: E402
from monitor.fluxion.abi import (  # noqa: E402
    TOPIC0_ORDER_FILLED,
    TOPIC0_V3_SWAP,
    TOPIC0_V3_SWAP_PCS,
)
from monitor.fluxion.events import decode_v3_swap_log  # noqa: E402
from monitor.fluxion.pools import PoolMeta  # noqa: E402
from monitor.fluxion.rpc import Rpc  # noqa: E402
from monitor.metrics.config import load_metrics_config  # noqa: E402
from monitor.metrics.session import session_kind  # noqa: E402
from monitor.symbols.multipliers import de_multiplied_price  # noqa: E402

TOPIC_TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
MANTLESCAN_TX = "https://mantlescan.xyz/tx/"
MANTLESCAN_ADDR = "https://mantlescan.xyz/address/"
BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"


def _load_pairs(path: Path) -> dict[str, Any]:
    """Load Bybit/Fluxion inventory (market file v2 or legacy flat pairs body)."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"pairs config root must be a mapping: {path}")
    # M7-2 market files nest the former pairs.yaml under inventory:.
    inv = raw.get("inventory")
    if isinstance(inv, dict):
        return inv
    return raw


def _pool_metas(pairs_doc: dict[str, Any]) -> list[PoolMeta]:
    usdc = pairs_doc["contracts"]["usdc"]
    out: list[PoolMeta] = []
    for p in pairs_doc["pairs"]:
        amm = (p.get("fluxion") or {}).get("amm") or {}
        pool = amm.get("pool")
        if not pool:
            continue
        f = p["fluxion"]
        out.append(
            PoolMeta(
                pair_id=p["id"],
                pool=pool.lower(),
                wrapper_token=f["wrapper_token"].lower(),
                native_token=f["native_token"].lower(),
                quote_token=usdc.lower(),
                native_decimals=int(f.get("native_decimals") or 18),
            )
        )
    return out


def _token_to_pair(pairs_doc: dict[str, Any]) -> dict[str, str]:
    m: dict[str, str] = {}
    for p in pairs_doc["pairs"]:
        f = p["fluxion"]
        m[f["native_token"].lower()] = p["id"]
        m[f["wrapper_token"].lower()] = p["id"]
    return m


def _chunked_logs(
    rpc: Rpc,
    *,
    address: str,
    topics: list[Any],
    start: int,
    end: int,
    step: int = 50_000,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    b = start
    while b <= end:
        e = min(b + step - 1, end)
        try:
            part = rpc.call(
                "eth_getLogs",
                [
                    {
                        "fromBlock": hex(b),
                        "toBlock": hex(e),
                        "address": address,
                        "topics": topics,
                    }
                ],
            )
            logs.extend(part)
        except Exception as exc:  # noqa: BLE001
            if step <= 2_000:
                raise RuntimeError(f"eth_getLogs failed {address} {b}-{e}: {exc}") from exc
            # shrink and retry this range
            mid = (b + e) // 2
            logs.extend(
                _chunked_logs(
                    rpc, address=address, topics=topics, start=b, end=mid, step=step // 2
                )
            )
            logs.extend(
                _chunked_logs(
                    rpc,
                    address=address,
                    topics=topics,
                    start=mid + 1,
                    end=e,
                    step=step // 2,
                )
            )
            b = e + 1
            continue
        b = e + 1
    return logs


def _block_ts_map(rpc: Rpc, block_numbers: set[int]) -> dict[int, int]:
    """Map block → timestamp; batch eth_getBlockByNumber."""
    out: dict[int, int] = {}
    blocks = sorted(block_numbers)
    batch_size = 40
    for i in range(0, len(blocks), batch_size):
        part = blocks[i : i + batch_size]
        calls = [("eth_getBlockByNumber", [hex(b), False]) for b in part]
        results = rpc.batch(calls)
        for b, res in zip(part, results, strict=True):
            if isinstance(res, dict) and res.get("timestamp") is not None:
                ts = res["timestamp"]
                out[b] = int(ts, 16) if isinstance(ts, str) else int(ts)
    return out


def _pool_token_order(rpc: Rpc, pool: str) -> tuple[str, str]:
    # token0() / token1()
    t0 = rpc.eth_call(pool, "0x0dfe1681")
    t1 = rpc.eth_call(pool, "0xd21220a7")
    a0 = "0x" + bytes.fromhex(t0[2:] if t0.startswith("0x") else t0)[-20:].hex()
    a1 = "0x" + bytes.fromhex(t1[2:] if t1.startswith("0x") else t1)[-20:].hex()
    return a0.lower(), a1.lower()


def fetch_swaps(
    rpc: Rpc,
    metas: list[PoolMeta],
    *,
    start: int,
    end: int,
    cache_path: Path,
) -> list[dict[str, Any]]:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    topics = [[TOPIC0_V3_SWAP, TOPIC0_V3_SWAP_PCS]]
    token_order: dict[str, tuple[str, str]] = {}
    rows: list[dict[str, Any]] = []
    for meta in metas:
        print(f"  swaps {meta.pair_id} …", flush=True)
        if meta.pool not in token_order:
            token_order[meta.pool] = _pool_token_order(rpc, meta.pool)
        t0, t1 = token_order[meta.pool]
        raw_logs = _chunked_logs(
            rpc, address=meta.pool, topics=topics, start=start, end=end
        )
        blocks = {int(lg["blockNumber"], 16) for lg in raw_logs}
        ts_map = _block_ts_map(rpc, blocks)
        for lg in raw_logs:
            bn = int(lg["blockNumber"], 16)
            tick = decode_v3_swap_log(
                lg,
                meta=meta,
                token0=t0,
                token1=t1,
                block_ts=ts_map.get(bn, 0),
                recv_ts_ms=ts_map.get(bn, 0) * 1000,
            )
            if tick is None:
                continue
            # wrapper delta for inventory (pool convention: + = pool received)
            if t0 == meta.wrapper_token.lower():
                wrapper_delta_pool = tick.amount_token0
            else:
                wrapper_delta_pool = tick.amount_token1
            # taker inventory change is opposite of pool wrapper change
            taker_delta = -wrapper_delta_pool
            rows.append(
                {
                    "pair_id": tick.pair_id,
                    "pool": tick.pool,
                    "block_number": tick.block_number,
                    "block_ts": tick.block_ts,
                    "ts_ms": tick.block_ts * 1000,
                    "tx_hash": tick.tx_hash.lower(),
                    "log_index": tick.log_index,
                    "sender": tick.sender.lower(),
                    "recipient": tick.recipient.lower(),
                    "direction": tick.direction,
                    "amount_token0": str(tick.amount_token0),
                    "amount_token1": str(tick.amount_token1),
                    "taker_delta_wrapper": str(taker_delta),
                    "price_usdc_per_wrapper": (
                        str(tick.price_usdc_per_wrapper)
                        if tick.price_usdc_per_wrapper is not None
                        else None
                    ),
                    "sqrt_price_x96": tick.sqrt_price_x96,
                }
            )
        print(f"    → {len([r for r in rows if r['pair_id']==meta.pair_id])}", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(rows))
    return rows


def fetch_rfq_fills(
    rpc: Rpc,
    *,
    lop: str,
    start: int,
    end: int,
    cache_path: Path,
    pairs_doc: dict[str, Any],
) -> list[dict[str, Any]]:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    print("  LOP OrderFilled …", flush=True)
    raw = _chunked_logs(
        rpc, address=lop, topics=[[TOPIC0_ORDER_FILLED]], start=start, end=end
    )
    blocks = {int(lg["blockNumber"], 16) for lg in raw}
    ts_map = _block_ts_map(rpc, blocks)
    usdc = pairs_doc["contracts"]["usdc"].lower()
    token_map = _token_to_pair(pairs_doc)
    rows: list[dict[str, Any]] = []
    for lg in raw:
        bn = int(lg["blockNumber"], 16)
        txh = str(lg["transactionHash"]).lower()
        receipt = rpc.get_transaction_receipt(txh) or {}
        logs = receipt.get("logs") or []
        decoded = decode_rfq_fill_from_receipt(
            tx_hash=txh,
            logs=logs,
            usdc=usdc,
            lop=lop,
            settlement_router="0x41dee1855293e4450cd67459047f372d4d818143",
            token_to_pair=token_map,
        )
        rows.append(
            {
                "block_number": bn,
                "block_ts": ts_map.get(bn, 0),
                "ts_ms": ts_map.get(bn, 0) * 1000,
                "tx_hash": txh,
                "log_index": int(lg["logIndex"], 16),
                "tx_from": str(receipt.get("from") or "").lower(),
                "tx_to": str(receipt.get("to") or "").lower(),
                "maker": decoded.maker,
                "taker": decoded.taker,
                "pair_id": decoded.pair_id,
                "token": decoded.token,
                "usdc_amount": str(decoded.usdc_amount) if decoded.usdc_amount else None,
                "stock_amount": (
                    str(decoded.stock_amount) if decoded.stock_amount else None
                ),
                "maker_side": decoded.maker_side,
                "external_addresses": list(decoded.external_addresses),
                "notes": list(decoded.notes),
            }
        )
    print(f"    → {len(rows)}", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(rows))
    return rows


def fetch_transfers(
    rpc: Rpc,
    pairs_doc: dict[str, Any],
    *,
    start: int,
    end: int,
    cache_path: Path,
) -> list[dict[str, Any]]:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    token_map = _token_to_pair(pairs_doc)
    # unique tokens
    tokens = sorted(token_map.keys())
    rows: list[dict[str, Any]] = []
    for tok in tokens:
        pid = token_map[tok]
        print(f"  Transfer {pid} {tok[:10]}…", flush=True)
        raw = _chunked_logs(
            rpc, address=tok, topics=[[TOPIC_TRANSFER]], start=start, end=end
        )
        blocks = {int(lg["blockNumber"], 16) for lg in raw}
        ts_map = _block_ts_map(rpc, blocks)
        for lg in raw:
            topics = lg.get("topics") or []
            if len(topics) < 3:
                continue
            frm = "0x" + str(topics[1])[-40:].lower()
            to = "0x" + str(topics[2])[-40:].lower()
            data = str(lg.get("data") or "0x")
            raw_b = bytes.fromhex(data[2:] if data.startswith("0x") else data)
            amt = Decimal(int.from_bytes(raw_b[-32:], "big")) / Decimal(10**18)
            bn = int(lg["blockNumber"], 16)
            rows.append(
                {
                    "pair_id": pid,
                    "token": tok,
                    "block_number": bn,
                    "block_ts": ts_map.get(bn, 0),
                    "ts_ms": ts_map.get(bn, 0) * 1000,
                    "tx_hash": str(lg["transactionHash"]).lower(),
                    "from": frm,
                    "to": to,
                    "amount": str(amt),
                }
            )
        print(f"    → {len([r for r in rows if r['token']==tok])}", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(rows))
    return rows


def fetch_bybit_mids(
    pairs_doc: dict[str, Any],
    *,
    start_ms: int,
    end_ms: int,
    cache_path: Path,
) -> dict[str, list[tuple[int, float]]]:
    """pair_id → [(ts_ms, de-multiplied mid), ...] from 1m klines."""
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        return {
            k: [(int(t), float(m)) for t, m in v] for k, v in raw.items()
        }
    out: dict[str, list[tuple[int, float]]] = {}
    with httpx.Client(timeout=30.0) as client:
        for p in pairs_doc["pairs"]:
            sym = p["bybit"]["symbol"]
            mult = Decimal(str(p["bybit"]["multiplier"]))
            pid = p["id"]
            print(f"  bybit kline {sym} …", flush=True)
            points: list[tuple[int, float]] = []
            cursor = start_ms
            while cursor < end_ms:
                r = client.get(
                    BYBIT_KLINE,
                    params={
                        "category": "spot",
                        "symbol": sym,
                        "interval": "1",
                        "start": cursor,
                        "end": min(cursor + 1000 * 60_000, end_ms),
                        "limit": 1000,
                    },
                )
                r.raise_for_status()
                body = r.json()
                rows = (body.get("result") or {}).get("list") or []
                if not rows:
                    break
                # Bybit returns newest first
                batch: list[tuple[int, float]] = []
                for row in rows:
                    # [start, open, high, low, close, volume, turnover]
                    ts = int(row[0])
                    o, h, low, c = (
                        Decimal(row[1]),
                        Decimal(row[2]),
                        Decimal(row[3]),
                        Decimal(row[4]),
                    )
                    mid = (h + low) / Decimal(2) if h > 0 and low > 0 else (o + c) / 2
                    dm = float(de_multiplied_price(mid, mult))
                    batch.append((ts, dm))
                batch.sort()
                points.extend(batch)
                # advance past last
                cursor = max(t for t, _ in batch) + 60_000
                if len(rows) < 1000:
                    break
                time.sleep(0.05)
            # dedupe
            by_ts = {t: m for t, m in points}
            out[pid] = sorted(by_ts.items())
            print(f"    → {len(out[pid])}", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({k: [[t, m] for t, m in v] for k, v in out.items()})
    )
    return out


def _nearest_mid(
    series: list[tuple[int, float]], ts_ms: int, *, max_skew_ms: int = 120_000
) -> Decimal | None:
    if not series:
        return None
    # binary search last <= ts
    lo, hi = 0, len(series) - 1
    best: tuple[int, float] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= ts_ms:
            best = series[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    if abs(best[0] - ts_ms) > max_skew_ms:
        return None
    return Decimal(str(best[1]))


def _session_for_ms(ts_ms: int, metrics_cfg: Any) -> str | None:
    if ts_ms <= 0:
        return None
    sk = session_kind(
        datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
        config=metrics_cfg,
    )
    return str(sk.value)


def build_events(
    swaps: list[dict[str, Any]],
    rfq: list[dict[str, Any]],
    transfers: list[dict[str, Any]],
    bybit: dict[str, list[tuple[int, float]]],
    metrics_cfg: Any,
    *,
    bybit_lookback_ms: int = 5000,
    bybit_min_move_bps: Decimal = Decimal("1"),
) -> tuple[list[InventoryEvent], list[TransferEdge]]:
    events: list[InventoryEvent] = []
    edges: list[TransferEdge] = []

    for s in swaps:
        ts_ms = int(s["ts_ms"])
        sess = _session_for_ms(ts_ms, metrics_cfg)
        flux_mid = (
            Decimal(s["price_usdc_per_wrapper"])
            if s.get("price_usdc_per_wrapper")
            else None
        )
        bmid = _nearest_mid(bybit.get(s["pair_id"], []), ts_ms)
        direction = s["direction"]
        conv: bool | None = None
        if direction in ("buy_native", "sell_native") and flux_mid and bmid:
            conv = is_converging(direction, fluxion_mid=flux_mid, bybit_mid=bmid)
        align: bool | None = None
        series = bybit.get(s["pair_id"], [])
        # lead-lag: mid ~5s before trade (M4 default lookback)
        prev = None
        if series:
            # convert list of (ts, float) for resolve helper
            mids = [(int(ts), Decimal(str(m))) for ts, m in series]
            prev = resolve_bybit_mid_prev(
                ts_ms, mids, lookback_ms=bybit_lookback_ms
            )
        if (
            direction in ("buy_native", "sell_native")
            and bmid is not None
            and prev is not None
        ):
            align = bybit_move_aligned(
                direction,
                bybit_mid=bmid,
                bybit_mid_prev=prev,
                min_move_bps=bybit_min_move_bps,
            )
        # notional ≈ |wrapper_delta| * price
        try:
            delta = Decimal(s["taker_delta_wrapper"])
            notional = abs(delta) * flux_mid if flux_mid else None
        except (ArithmeticError, ValueError, TypeError):
            delta = Decimal(0)
            notional = None
        events.append(
            InventoryEvent(
                ts_ms=ts_ms,
                pair_id=s["pair_id"],
                address=s["recipient"],
                delta_native=delta,
                kind=LedgerKind.AMM_SWAP,
                tx_hash=s["tx_hash"],
                direction=direction if direction != "unknown" else None,
                notional_usd=notional,
                converges=conv,
                bybit_align=align,
                session=sess,
                role="taker",
                counterparty=s["sender"],
            )
        )

    for f in rfq:
        ts_ms = int(f["ts_ms"])
        sess = _session_for_ms(ts_ms, metrics_cfg)
        pair_id = f.get("pair_id") or "UNKNOWN"
        stock = Decimal(f["stock_amount"]) if f.get("stock_amount") else Decimal(0)
        usdc = Decimal(f["usdc_amount"]) if f.get("usdc_amount") else None
        maker_side = f.get("maker_side")
        # maker inventory: if maker sells native, delta negative
        if f.get("maker"):
            if maker_side == "sell_native":
                m_delta = -stock
                t_delta = stock
                t_dir = "buy_native"
            elif maker_side == "buy_native":
                m_delta = stock
                t_delta = -stock
                t_dir = "sell_native"
            else:
                m_delta = Decimal(0)
                t_delta = Decimal(0)
                t_dir = None
            events.append(
                InventoryEvent(
                    ts_ms=ts_ms,
                    pair_id=pair_id,
                    address=f["maker"],
                    delta_native=m_delta,
                    kind=LedgerKind.RFQ_FILL,
                    tx_hash=f["tx_hash"],
                    direction=maker_side,
                    notional_usd=usdc,
                    converges=None,
                    session=sess,
                    role="maker",
                    counterparty=f.get("taker"),
                )
            )
            if f.get("taker"):
                events.append(
                    InventoryEvent(
                        ts_ms=ts_ms,
                        pair_id=pair_id,
                        address=f["taker"],
                        delta_native=t_delta,
                        kind=LedgerKind.RFQ_FILL,
                        tx_hash=f["tx_hash"],
                        direction=t_dir,
                        notional_usd=usdc,
                        converges=None,
                        session=sess,
                        role="taker",
                        counterparty=f.get("maker"),
                    )
                )

    for t in transfers:
        ts_ms = int(t["ts_ms"])
        sess = _session_for_ms(ts_ms, metrics_cfg)
        amt = Decimal(t["amount"])
        edge = TransferEdge(
            ts_ms=ts_ms,
            pair_id=t["pair_id"],
            token=t["token"],
            frm=t["from"],
            to=t["to"],
            amount=amt,
            tx_hash=t["tx_hash"],
        )
        edges.append(edge)
        # skip mint/burn zero address
        zero = "0x" + "0" * 40
        if t["from"] != zero:
            events.append(
                InventoryEvent(
                    ts_ms=ts_ms,
                    pair_id=t["pair_id"],
                    address=t["from"],
                    delta_native=-amt,
                    kind=LedgerKind.ERC20_TRANSFER,
                    tx_hash=t["tx_hash"],
                    session=sess,
                    role="transfer_counterparty",
                    counterparty=t["to"],
                )
            )
        if t["to"] != zero:
            events.append(
                InventoryEvent(
                    ts_ms=ts_ms,
                    pair_id=t["pair_id"],
                    address=t["to"],
                    delta_native=amt,
                    kind=LedgerKind.ERC20_TRANSFER,
                    tx_hash=t["tx_hash"],
                    session=sess,
                    role="transfer_counterparty",
                    counterparty=t["from"],
                )
            )
    return events, edges


def classify_contracts(
    rpc: Rpc, addrs: list[str], *, batch_size: int = 50
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    batch = max(1, batch_size)
    for i in range(0, len(addrs), batch):
        part = addrs[i : i + batch]
        calls = [("eth_getCode", [a, "latest"]) for a in part]
        try:
            results = rpc.batch(calls)
        except Exception as exc:  # noqa: BLE001
            print(f"  eth_getCode batch failed: {exc}", flush=True)
            continue
        for a, code in zip(part, results, strict=True):
            c = str(code or "").strip().lower()
            out[a] = c not in ("", "0x", "0x0")
    return out


def run_analysis(
    events: list[InventoryEvent],
    edges: list[TransferEdge],
    rfq_rows: list[dict[str, Any]],
    contract_flags: dict[str, bool],
    *,
    infra: set[str],
) -> dict[str, Any]:
    # CEX clustering excluding known infra + pools
    cex = cluster_cex_candidates(
        edges,
        min_counterparties=4,
        min_transfers=6,
        exclude=infra,
    )
    # Never treat infra as a CEX wallet even if it slipped through.
    cex_addrs = [c.address for c in cex[:20] if c.address not in infra]
    # Do not let cluster hubs label themselves as rebalancer via self-degree.
    cex_hub_set = set(cex_addrs)

    by_addr_n: dict[str, int] = defaultdict(int)
    for e in events:
        if e.address.lower() in infra:
            continue
        by_addr_n[e.address.lower()] += 1

    th = DraftThresholds()
    labeled = []
    for addr, _n in sorted(by_addr_n.items(), key=lambda kv: -kv[1]):
        feats = aggregate_address_features(
            events, address=addr, is_contract=contract_flags.get(addr)
        )
        touches = count_cex_touches(
            edges,
            addr,
            cex_addrs,
            min_amount=th.reb_min_transfer_notional_native,
        )
        # Cluster hubs are CEX candidates themselves — not rebalancer users.
        if addr in cex_hub_set:
            touches = 0
        lab = assign_draft_label(feats, thresholds=th, cex_touch_transfers=touches)
        labeled.append(
            {
                "address": addr,
                "label": lab.label.value,
                "reasons": list(lab.reasons),
                "is_contract": feats.is_contract,
                "n_pairs": feats.n_pairs,
                "pairs": list(feats.pairs),
                "n_amm": feats.n_amm,
                "n_rfq_maker": feats.n_rfq_maker,
                "n_rfq_taker": feats.n_rfq_taker,
                "n_transfer": feats.n_transfer,
                "n_buy": feats.n_buy,
                "n_sell": feats.n_sell,
                "notional_usd": str(feats.notional_usd),
                "median_notional_usd": str(feats.median_notional_usd),
                "convergence_ratio": feats.convergence_ratio,
                "n_convergence_scored": feats.n_convergence_scored,
                "inv_mean_reversion": feats.inv_mean_reversion,
                "open_share": feats.open_share,
                "cex_touches": touches,
            }
        )

    # top candidates per label
    by_label: dict[str, list] = defaultdict(list)
    for row in labeled:
        by_label[row["label"]].append(row)

    th_dict = {
        k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(th).items()
    }
    cex_rows = []
    for c in cex[:15]:
        d = asdict(c)
        d["volume"] = str(d["volume"])
        cex_rows.append(d)
    return {
        "n_events": len(events),
        "n_transfers": len(edges),
        "n_rfq": len(rfq_rows),
        "n_addresses": len(labeled),
        "cex_clusters": cex_rows,
        "labeled": labeled,
        "by_label_counts": {k: len(v) for k, v in by_label.items()},
        "thresholds": th_dict,
        "rfq_sample": rfq_rows[:50],
    }


def render_report(
    result: dict[str, Any],
    *,
    meta: dict[str, Any],
    out_path: Path,
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append("# MM attribution analysis (WHI-767)")
    lines.append("")
    lines.append(
        "Research note: per-address inventory ledger + draft "
        "`market_maker` / `rebalancer` rules for Fluxion xStocks, with evidence "
        "from a full-history chain backfill (collector journal alone is too short "
        "/ weekend-sparse)."
    )
    lines.append("")
    lines.append(f"**Generated:** {now}")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|------|--------|")
    lines.append(f"| Window | {meta.get('days')} days ending block `{meta.get('head')}` |")
    lines.append(f"| From block | `{meta.get('start_block')}` |")
    lines.append(f"| Pools (AMM) | {meta.get('n_pools')} liquid inventory pairs |")
    lines.append(f"| AMM swaps decoded | {meta.get('n_swaps')} |")
    lines.append(f"| LOP OrderFilled | {meta.get('n_rfq')} |")
    lines.append(f"| ERC-20 Transfer (native+wrapper) | {meta.get('n_transfers')} |")
    lines.append(f"| Distinct addresses (ledger) | {result['n_addresses']} |")
    lines.append(
        "| Bybit mids | public 1m klines, de-multiplied by "
        "`pairs.yaml` xstockMultiplier |"
    )
    lines.append(
        "| Session | `monitor.metrics.session.session_kind` (NYSE calendar) |"
    )
    lines.append(
        "| Repro | `uv run python scripts/mm_attribution_analysis.py` "
        "(cache under `data/mm_analysis/`) |"
    )
    lines.append(
        "| Pure rules | `monitor.attribution.mm_draft` "
        "(unit-tested; productize in WHI-768) |"
    )
    lines.append("")
    lines.append("### Inventory sign convention")
    lines.append("")
    lines.append(
        "- **AMM:** taker = Swap `recipient` (M4). "
        "`delta_native` ≈ −Δwrapper_pool (buy_native → positive inventory)."
    )
    lines.append(
        "- **RFQ:** maker/taker from receipt Transfer/Approval graph "
        "(see § RFQ decode). Maker providing stock → `sell_native`."
    )
    lines.append(
        "- **Transfer:** `to` +amount, `from` −amount (native and wrapper both "
        "map to the same `pair_id` — double-count risk noted in data gaps)."
    )
    lines.append("")
    lines.append("## Label counts (draft priority order)")
    lines.append("")
    lines.append("| Label | Addresses |")
    lines.append("|-------|----------:|")
    for lab in (
        "market_maker",
        "arb_bot",
        "rebalancer",
        "price_keeper",
        "retail",
        "unknown",
    ):
        lines.append(f"| `{lab}` | {result['by_label_counts'].get(lab, 0)} |")
    lines.append("")

    def _top(label: str, n: int = 15) -> list[dict[str, Any]]:
        rows = [r for r in result["labeled"] if r["label"] == label]
        rows.sort(
            key=lambda r: (
                -r["n_rfq_maker"],
                -r["n_amm"],
                -r["cex_touches"],
                r["address"],
            )
        )
        return rows[:n]

    lines.append("## Candidate addresses (evidence)")
    lines.append("")
    lines.append(
        "Addresses are lowercased. Explore on "
        f"[Mantlescan]({MANTLESCAN_ADDR}). Tx evidence links use the same explorer."
    )
    lines.append("")

    for label, title in (
        ("market_maker", "Market makers"),
        ("rebalancer", "Rebalancers (CEX-touch)"),
        ("arb_bot", "Arb bots (high convergence)"),
        ("price_keeper", "Price keepers (small bidirectional AMM)"),
    ):
        rows = _top(label, 20)
        lines.append(f"### {title} (`{label}`)")
        lines.append("")
        if not rows:
            lines.append("_None above draft thresholds in this window._")
            lines.append("")
            continue
        lines.append(
            "| Address | Contract? | Pairs | AMM | RFQ m/t | "
            "Conv | Mean-rev | CEX touches | Why |"
        )
        lines.append(
            "|---------|-----------|------:|----:|--------:|"
            "-----:|---------:|------------:|-----|"
        )
        for r in rows:
            conv = (
                f"{r['convergence_ratio']:.2f} (n={r['n_convergence_scored']})"
                if r["convergence_ratio"] is not None
                else "—"
            )
            mr = (
                f"{r['inv_mean_reversion']:.2f}"
                if r["inv_mean_reversion"] is not None
                else "—"
            )
            cc = r["is_contract"]
            cc_s = "yes" if cc is True else ("no" if cc is False else "?")
            why = "; ".join(r["reasons"])[:80]
            lines.append(
                f"| [`{r['address'][:10]}…`]({MANTLESCAN_ADDR}{r['address']}) "
                f"| {cc_s} | {r['n_pairs']} "
                f"({','.join(r['pairs'][:4])}"
                f"{'…' if len(r['pairs']) > 4 else ''}) "
                f"| {r['n_amm']} | {r['n_rfq_maker']}/{r['n_rfq_taker']} "
                f"| {conv} | {mr} | {r['cex_touches']} | {why} |"
            )
        lines.append("")
        # top-3 deep evidence
        for r in rows[:5]:
            lines.append(f"#### `{r['address']}`")
            lines.append("")
            lines.append(f"- Label: **{r['label']}**")
            lines.append(f"- Reasons: {', '.join(r['reasons']) or '—'}")
            lines.append(
                f"- Activity: AMM={r['n_amm']} buy={r['n_buy']} sell={r['n_sell']} "
                f"RFQ maker={r['n_rfq_maker']} taker={r['n_rfq_taker']} "
                f"transfers={r['n_transfer']}"
            )
            lines.append(
                f"- Notional (sum USD proxy): {r['notional_usd']} "
                f"(median {r['median_notional_usd']})"
            )
            lines.append(
                f"- Explorer: {MANTLESCAN_ADDR}{r['address']}"
            )
            lines.append("")

    lines.append("## RFQ maker evidence (strongest MM signal)")
    lines.append("")
    lines.append(
        f"Decoded **{result['n_rfq']}** LOP `OrderFilled` events. "
        "Maker is recovered from Approval→LOP or first external stock sender; "
        "taker is the other non-infra external on the Transfer graph. "
        "Settlement routinely routes through "
        "`0x41dee1855293e4450cd67459047f372d4d818143` and fee hops "
        "`0x5f7a4c11…` / `0x3fdcb4af…` / `0x68a35992…` — treated as infra, not MM."
    )
    lines.append("")
    rfq = result.get("rfq_sample") or []
    if rfq:
        lines.append("| ts (UTC) | pair | maker | taker | side | USDC | tx |")
        lines.append("|----------|------|-------|-------|------|-----:|----|")
        for f in rfq[:25]:
            ts = (
                datetime.fromtimestamp(int(f["block_ts"]), UTC).strftime("%Y-%m-%d %H:%M")
                if f.get("block_ts")
                else "?"
            )
            maker = f.get("maker") or "—"
            taker = f.get("taker") or "—"
            m_s = f"`{maker[:10]}…`" if maker != "—" else "—"
            t_s = f"`{taker[:10]}…`" if taker != "—" else "—"
            lines.append(
                f"| {ts} | {f.get('pair_id') or '?'} | {m_s} | {t_s} | "
                f"{f.get('maker_side') or '?'} | {f.get('usdc_amount') or '—'} | "
                f"[tx]({MANTLESCAN_TX}{f['tx_hash']}) |"
            )
        lines.append("")
    else:
        lines.append("_No RFQ fills in window._")
        lines.append("")

    # RFQ maker leaderboard from full labeled addresses
    mm_rfq = [
        r
        for r in result["labeled"]
        if r["n_rfq_maker"] > 0
    ]
    mm_rfq.sort(key=lambda r: -r["n_rfq_maker"])
    if mm_rfq:
        lines.append("### RFQ maker leaderboard (all decoded fills)")
        lines.append("")
        lines.append("| Maker | Fills as maker | Label | Pairs |")
        lines.append("|-------|---------------:|-------|-------|")
        for r in mm_rfq[:20]:
            lines.append(
                f"| [`{r['address'][:12]}…`]({MANTLESCAN_ADDR}{r['address']}) "
                f"| {r['n_rfq_maker']} | `{r['label']}` | {','.join(r['pairs'])} |"
            )
        lines.append("")

    lines.append("## Rebalance / CEX deposit clustering")
    lines.append("")
    lines.append(
        "Heuristic: addresses with high unique-counterparty degree on "
        "native/wrapper Transfer graphs (deposit-like). **Not** verified "
        "Mantlescan labels — candidates for manual check + hot-wallet list "
        "in WHI-768."
    )
    lines.append("")
    clusters = result.get("cex_clusters") or []
    if clusters:
        lines.append("| Address | Counterparties | Transfers | Volume (token units) |")
        lines.append("|---------|---------------:|----------:|---------------------:|")
        for c in clusters[:12]:
            # volume may be Decimal serialized
            vol = c.get("volume")
            lines.append(
                f"| [`{c['address'][:12]}…`]({MANTLESCAN_ADDR}{c['address']}) "
                f"| {c['n_counterparties']} | {c['n_transfers']} | {vol} |"
            )
        lines.append("")
    else:
        lines.append(
            "_No address cleared the clustering gate "
            "(≥4 counterparties, ≥6 transfers) after infra exclusion — "
            "Transfer volume is still thin; widen window or lower gates for dogfood._"
        )
        lines.append("")

    lines.append("## Draft machine-checkable rules (feed WHI-768)")
    lines.append("")
    lines.append(
        "Priority order (first match wins): market_maker → arb_bot → "
        "rebalancer → price_keeper → retail → unknown. Thresholds are "
        "defaults in `DraftThresholds` (M4 gates match "
        "`config/attribution.yaml`)."
    )
    lines.append("")
    th = result["thresholds"]
    lines.append("### `market_maker`")
    lines.append("")
    lines.append("Either:")
    lines.append("")
    lines.append(
        f"1. **RFQ maker path (strong):** `n_rfq_maker ≥ {th['mm_min_rfq_maker_fills']}` "
        "on decoded LOP fills (maker = Approval→LOP owner or first external stock sender)."
    )
    lines.append(
        f"2. **Cross-pair inventory path:** `n_pairs ≥ {th['mm_min_pairs']}` AND "
        f"`n_amm ≥ {th['mm_min_amm_trades']}` AND both directions with share "
        f"≥ {th['mm_min_direction_share']} AND "
        f"`median_notional_usd ≤ {th['mm_max_median_notional_usd']}` AND "
        f"(`inv_mean_reversion` is null OR ≥ {th['mm_min_mean_reversion']})."
    )
    lines.append("")
    lines.append(
        "**Relation to M4 `price_keeper`:** price_keeper is the *small* "
        "bidirectional AMM re-pegger without RFQ maker evidence. An address that "
        "also clears RFQ-maker or cross-pair mean-reverting inventory upgrades to "
        "`market_maker`. Do not double-label."
    )
    lines.append("")
    lines.append("### `rebalancer`")
    lines.append("")
    lines.append(
        f"`cex_touch_transfers ≥ {th['reb_min_cex_touch_transfers']}` where a touch is "
        "a native/wrapper Transfer whose counterparty ∈ CEX cluster set "
        f"(cluster gate: degree/volume heuristics; min notional "
        f"{th['reb_min_transfer_notional_native']} native when filtering noise)."
    )
    lines.append("")
    lines.append(
        "Orthogonal to MM: the same desk may be both; product may emit "
        "`market_maker+rebalancer` flags instead of mutually exclusive labels."
    )
    lines.append("")
    lines.append("### Existing M4 labels (unchanged semantics)")
    lines.append("")
    lines.append(
        f"- **`arb_bot`:** `n_convergence_scored ≥ {th['arb_min_scored']}` and "
        f"`convergence_ratio ≥ {th['arb_min_convergence']}` "
        "(same spirit as `m4-attribution-labels.md`)."
    )
    lines.append(
        f"- **`price_keeper`:** `n_amm ≥ {th['pk_min_trades']}`, both dirs ≥ "
        f"{th['pk_min_direction_share']}, "
        f"median ≤ {th['pk_max_median_notional_usd']}, "
        f"max ≤ {th['pk_max_trade_notional_usd']}."
    )
    lines.append(
        f"- **`retail` / `unknown`:** residual with `n_trade ≥ {th['retail_min_trades']}` "
        "vs thin sample."
    )
    lines.append("")
    lines.append("### Fields required in the collector (WHI-768 input)")
    lines.append("")
    lines.append("| Field | Source | Why |")
    lines.append("|-------|--------|-----|")
    lines.append(
        "| `fluxion_rfq_fills.maker` | receipt decode / LOP order | strongest MM signal |"
    )
    lines.append(
        "| `fluxion_rfq_fills.taker` | receipt decode | taker behavior / flow |"
    )
    lines.append(
        "| `fluxion_rfq_fills.pair_id` | token map | pair-scoped RFQ share |"
    )
    lines.append(
        "| `fluxion_rfq_fills.direction` / amounts | Transfer legs | inventory + notional |"
    )
    lines.append(
        "| `erc20_transfers` (native + wrapper) | `eth_getLogs` Transfer | "
        "rebalance + inventory completeness |"
    )
    lines.append(
        "| optional `cex_wallets` allowlist | config | stable rebalance label |"
    )
    lines.append(
        "| existing `fluxion_swaps.*` + Bybit mid join | already collected | "
        "convergence / arb_bot |"
    )
    lines.append("")
    lines.append("## Data gaps / caveats")
    lines.append("")
    lines.append(
        "1. **Collector journal (VPS) is not the analysis source for trades.** "
        "As of analysis time it held ~14h of tape starting 2026-08-01 (weekend) "
        "with **1** AMM swap and **0** RFQ fills. Historical AMM/RFQ/Transfer "
        "were backfilled via Mantle `eth_getLogs`."
    )
    lines.append(
        "2. **Wrapper vs native double-count:** Transfer pull includes both "
        "native and wrapper ERC-20s under the same `pair_id`. Inventory path "
        "for wrap/unwrap can double-move; WHI-768 should pick one inventory "
        "asset (prefer native) or net wrap events."
    )
    lines.append(
        "3. **RFQ token map misses:** 16/18 fills in the 30d sample touch "
        "ERC-20s **outside** inventory native/wrapper "
        "(top: `0x7796f4e2…`, `0x58100046…`, `0x368192fe…`, `0x90a2a4c7…`) "
        "→ `pair_id` often null / `UNKNOWN`. Maker recovery still works; "
        "WHI-768 should resolve token→pair via `asset()` on wrappers or an "
        "expanded token registry before pair-scoped RFQ share ships."
    )
    lines.append(
        "4. **Bybit mid join is 1m kline**, not the live L1 book — fine for "
        "convergence ratios over days, not for sub-second lead-lag."
    )
    lines.append(
        "5. **Address clustering is partial.** Contract vs EOA is probed for "
        "up to 200 ledger addresses; deployer/funding-source traces and "
        "behavior-similarity clustering are **not** implemented in this "
        "pass (scope cut for WHI-767; WHI-768 may pick them up)."
    )
    lines.append(
        "6. **CEX wallets are clustered, not labeled.** Manual Mantlescan / "
        "Bybit deposit address verification still required before shipping "
        "`rebalancer` as a product label."
    )
    lines.append(
        "7. **LP Mint/Burn not pulled.** MM LP behavior is out of scope for "
        "this pass; only swap/fill/transfer inventory."
    )
    lines.append(
        "8. **Pools without AMM** (AMZNx/COINx/MCDx) contribute Transfer-only "
        "rows; no swap-based convergence."
    )
    lines.append(
        "9. **RFQ-maker `market_maker` path** is fitted on a thin "
        "sample (18 LOP fills / 30d). Threshold `mm_min_rfq_maker_fills=2` "
        "is research-default; re-validate before productization."
    )
    lines.append(
        "10. **Cross-pair MM inventory path** (bidirectional + mean-reversion "
        "across ≥2 pairs) is implemented but did **not** fire in the 30d "
        "sample — all five `market_maker` hits came from the RFQ-maker path. "
        "Thresholds for that branch are unfitted on live xStock flow."
    )
    lines.append("")
    lines.append("## Relationship to M4")
    lines.append("")
    lines.append(
        "M4 (`arb_bot` / `price_keeper` / `retail` / `unknown`) remains the "
        "behavior layer for **AMM takers**. This research adds:"
    )
    lines.append("")
    lines.append("- **RFQ maker** as a first-class economic role (not a taker).")
    lines.append("- **Inventory mean-reversion** and **cross-pair** activity.")
    lines.append("- **CEX transfer touches** for rebalance.")
    lines.append("")
    lines.append(
        "Product recommendation for WHI-768: keep M4 labels for AMM takers; "
        "add orthogonal flags `is_rfq_maker`, `is_rebalancer`, and promote "
        "combined MM when RFQ-maker or cross-pair inventory rules fire."
    )
    lines.append("")
    lines.append("## Reproduction")
    lines.append("")
    lines.append("```bash")
    lines.append("# optional keyed RPC")
    lines.append("export MANTLE_RPC_URL=...   # wss-tob rewritten to https rpc-tob")
    lines.append("uv run python scripts/mm_attribution_analysis.py --days 30")
    lines.append("# reuse cache:")
    lines.append("uv run python scripts/mm_attribution_analysis.py --skip-fetch")
    lines.append("```")
    lines.append("")
    lines.append("Artifacts:")
    lines.append("")
    lines.append(
        "- `data/mm_analysis/swaps.json`, `rfq_fills.json`, "
        "`transfers.json`, `bybit_mids.json`"
    )
    lines.append("- `data/mm_analysis/result.json` — full labeled table")
    lines.append("- `src/monitor/attribution/mm_draft.py` — pure rules")
    lines.append("- `tests/monitor/attribution/test_mm_draft.py`")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--cache-dir", type=Path, default=_REPO / "data" / "mm_analysis")
    ap.add_argument(
        "--report",
        type=Path,
        default=_REPO / "docs" / "references" / "mm-attribution-analysis.md",
    )
    ap.add_argument("--skip-fetch", action="store_true", help="use cache only")
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild markdown from result.json only",
    )
    ap.add_argument(
        "--pairs",
        type=Path,
        default=_REPO / "config" / "markets" / "bybit-fluxion.yaml",
        help="Market inventory (default: config/markets/bybit-fluxion.yaml)",
    )
    args = ap.parse_args()

    load_dotenv(_REPO)

    cache = args.cache_dir
    cache.mkdir(parents=True, exist_ok=True)

    if args.report_only:
        result = json.loads((cache / "result.json").read_text())
        meta = json.loads((cache / "meta.json").read_text())
        # convert Decimal fields in thresholds if needed
        render_report(result, meta=meta, out_path=args.report)
        return 0

    pairs_doc = _load_pairs(args.pairs)
    metas = _pool_metas(pairs_doc)
    metrics_cfg = load_metrics_config()
    attr_cfg = load_attribution_config()

    rpc_url = resolve_mantle_rpc_url()
    print(f"rpc={rpc_url[:32]}… days={args.days}", flush=True)
    rpc = Rpc(rpc_url, min_interval=0.05)
    try:
        head = rpc.block_number()
        # ~2s blocks on Mantle
        start = max(0, head - args.days * 86_400 // 2)
        meta = {
            "days": args.days,
            "head": head,
            "start_block": start,
            "n_pools": len(metas),
            "generated_at": datetime.now(UTC).isoformat(),
        }

        if args.skip_fetch:
            swaps = json.loads((cache / "swaps.json").read_text())
            rfq = json.loads((cache / "rfq_fills.json").read_text())
            transfers = json.loads((cache / "transfers.json").read_text())
            bybit = {
                k: [(int(t), float(m)) for t, m in v]
                for k, v in json.loads((cache / "bybit_mids.json").read_text()).items()
            }
        else:
            print("fetch swaps", flush=True)
            swaps = fetch_swaps(
                rpc, metas, start=start, end=head, cache_path=cache / "swaps.json"
            )
            print("fetch rfq", flush=True)
            rfq = fetch_rfq_fills(
                rpc,
                lop=pairs_doc["contracts"]["limit_order_protocol"],
                start=start,
                end=head,
                cache_path=cache / "rfq_fills.json",
                pairs_doc=pairs_doc,
            )
            print("fetch transfers", flush=True)
            transfers = fetch_transfers(
                rpc,
                pairs_doc,
                start=start,
                end=head,
                cache_path=cache / "transfers.json",
            )
            # time range for bybit
            ts_list = [int(s["ts_ms"]) for s in swaps if s.get("ts_ms")]
            ts_list += [int(t["ts_ms"]) for t in transfers if t.get("ts_ms")]
            if not ts_list:
                start_ms = int(time.time() * 1000) - args.days * 86_400_000
                end_ms = int(time.time() * 1000)
            else:
                start_ms = min(ts_list)
                end_ms = max(ts_list)
            print("fetch bybit mids", flush=True)
            bybit = fetch_bybit_mids(
                pairs_doc,
                start_ms=start_ms,
                end_ms=end_ms,
                cache_path=cache / "bybit_mids.json",
            )

        meta["n_swaps"] = len(swaps)
        meta["n_rfq"] = len(rfq)
        meta["n_transfers"] = len(transfers)

        print("build events", flush=True)
        events, edges = build_events(
            swaps,
            rfq,
            transfers,
            bybit,
            metrics_cfg,
            bybit_lookback_ms=attr_cfg.bybit_correlation.lookback_ms,
            bybit_min_move_bps=attr_cfg.bybit_correlation.min_move_bps,
        )

        infra = {
            pairs_doc["contracts"]["usdc"].lower(),
            pairs_doc["contracts"]["limit_order_protocol"].lower(),
            pairs_doc["contracts"]["fluxion_v3_router"].lower(),
            pairs_doc["contracts"]["fluxion_v3_factory"].lower(),
            pairs_doc["contracts"]["fluxion_v3_quoter"].lower(),
            "0x41dee1855293e4450cd67459047f372d4d818143",
            "0x5f7a4c11bde4f218f0025ef444c369d838ffa2ad",
            "0x3fdcb4af7f9019f6f8b669790bde93bbf91bbcc0",
            "0x68a359928fa70d17207c5d7e47a9047c0744d6f4",
            "0x788d911ae7c95121a89a0f0306db65d87422e1de",
            "0x" + "0" * 40,
        }
        for p in pairs_doc["pairs"]:
            f = p["fluxion"]
            infra.add(f["native_token"].lower())
            infra.add(f["wrapper_token"].lower())
            amm = f.get("amm") or {}
            if amm.get("pool"):
                infra.add(str(amm["pool"]).lower())

        # Prefer addresses that will be labeled (non-infra ledger participants).
        addr_set = sorted(
            {e.address.lower() for e in events if e.address.lower() not in infra}
        )
        print(f"classify {min(len(addr_set), 200)} / {len(addr_set)} addresses", flush=True)
        flags = classify_contracts(
            rpc, addr_set[:200], batch_size=attr_cfg.rpc_probe_batch_size
        )

        print("score", flush=True)
        result = run_analysis(events, edges, rfq, flags, infra=infra)
        # keep full rfq list for report
        result["rfq_sample"] = rfq

        # JSON-serialize thresholds Decimals
        def _jsonable(o: Any) -> Any:
            if isinstance(o, Decimal):
                return str(o)
            if isinstance(o, dict):
                return {k: _jsonable(v) for k, v in o.items()}
            if isinstance(o, list):
                return [_jsonable(v) for v in o]
            return o

        (cache / "result.json").write_text(json.dumps(_jsonable(result), indent=2))
        (cache / "meta.json").write_text(json.dumps(meta, indent=2))
        render_report(result, meta=meta, out_path=args.report)
        print(
            f"done swaps={meta['n_swaps']} rfq={meta['n_rfq']} "
            f"xfer={meta['n_transfers']} addrs={result['n_addresses']} "
            f"labels={result['by_label_counts']}",
            flush=True,
        )
    finally:
        rpc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
