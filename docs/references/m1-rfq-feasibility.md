# M1 RFQ feasibility (WHI-730)

Decision recorded for M2 collectors and the TUI RFQ column (`docs/DESIGN.md` §2.2 / §8).

## Conclusion

**Mode: `pollable_quote`**

xChange Atomic RFQ on Fluxion exposes a **public EXACT_INPUT quote API** that can be
polled without trading credentials. M2 should treat RFQ as a live quote column, not
degrade to “last on-chain fill only.”

Config mirror: `config/pairs.yaml` → `rfq.mode: pollable_quote`.

## Endpoints (verified 2026-07-31)

| Role | URL |
|------|-----|
| Preferred same-origin proxy | `POST https://fluxion.network/api/limit-order/quote` |
| Upstream public proxy | `POST https://fluxion-proxy-api-production.up.railway.app/quote` |

No API key required on either path. Upstream is rate-limited to **60 req/min**.
`config/pairs.yaml` sets `min_poll_interval_s: 11` so that polling all **11** inventory
pairs once each stays under that global budget (`11 * (60/11) ≈ 60`). Do not tighten
the interval without shrinking the polled set or raising the budget.

### Request

```json
{
  "tokenIn": "0x…",
  "tokenOut": "0x…",
  "amount": "100000000",
  "type": "EXACT_INPUT"
}
```

Rules:

- Counter-asset for RFQ is **USDC** on Mantle (`0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9`).
- `amount` is base units of `tokenIn` (USDC = 6 decimals; native xStocks = 18).
- Exact-output RFQ is not documented.

### Response (shape)

```json
{
  "side": "buy",
  "chainId": 5000,
  "amountIn": "100000000",
  "amountOut": "…",
  "price": "310.63…",
  "requestId": "uuid",
  "tokenIn": "0x…",
  "tokenOut": "0x…",
  "createdAt": "…",
  "rawPrice": "…",
  "rawAmountOut": "…"
}
```

Use `amountOut` / `price` as the executable quote. Treat `raw*` as pre-adjustment
references only. HTTP `204` means no executable RFQ at that moment (not an error for a
resting limit order, but not a displayable mid either).

Smoke test (TSLAx native / 100 USDC buy, 2026-07-31): HTTP 200 with `side=buy`,
`chainId=5000`, price ≈ token mid.

## On-chain settlement / attribution events

RFQ-priced trades settle through the **Fluxion Limit Order Protocol** (1inch LOP v4
lineage), not as Uniswap V3 `Swap` logs on the AMM pool.

| Item | Value |
|------|--------|
| Contract | `0x11de6011345586785810e52448a44c6595eedc18` |
| EIP-712 name / version | `Fluxion Limit Order Protocol` / `4` |
| Chain | Mantle `5000` |
| Bytecode present | yes (`eth_getCode` non-empty, ~34k hex chars) |

### Event shape (M1 lock for M4 classification)

Mechanism split (product-level):

| Mechanism | On-chain footprint | Attribution label |
|-----------|--------------------|-------------------|
| RFQ / Atomic RFQ | Logs from **Limit Order Protocol** only | MM-driven |
| AMM | UniV3-style `Swap` on Fluxion V3 pools | active taker |

Canonical **1inch Limit Order Protocol v4** fill / cancel topics (keccak of the
standard signatures — Fluxion’s LOP is EIP-712 v4 / same family). Use these as the
starting topic0 filter when M2/M4 wire log collection; confirm against the first live
fill before treating as final:

| Signature | topic0 |
|-----------|--------|
| `OrderFilled(bytes32,uint256)` | `0xfec331350fce78ba658e082a71da20ac9f8d798a99b3c79681c8440cbfe77e07` |
| `OrderCancelled(bytes32)` | `0x5152abf959f6564662358c2e52b702259b78bac5ee7842a0f01937e670efcc7d` |
| `BitInvalidatorUpdated(address,uint256,uint256)` | `0xcda0f7e73d07bdb14b141f2cf4745926629a1b63e7c6a3dd8a80232cb459a850` |

**Live verification gap (carry to M2):** `eth_getLogs` over the LOP for the last ~200k
Mantle blocks (~4.6d at 2s/block) returned **zero** logs at inventory time, so the
topic0 table above is **signature-derived, not fill-observed**. M2 should capture the
first real fill (or deploy a one-shot probe order off-band) and lock the observed
topic0 + ABI-decoded field layout in `docs/references/` before M4 heuristics ship.

Order build/submit (execution) is out of scope for this read-only panel; URLs exist under
`fluxion.network/api/limit-order/orderbook/v4.1/5000` for completeness.

## Implications for M2

1. Collector: poll RFQ quote for each monitored native xStock ↔ USDC pair (respect rate
   limit); surface stale/204 as “RFQ unavailable,” not as a zero price.
2. AMM path remains independent: Fluxion V3 wrapper/USDC pools via factory
   `0xF883162Ed9c7E8EF604214c964c678E40c9B737C` (UniV3-compatible; fee tiers
   100/500/3000/10000 observed; liquid xStock pools use **3000**).
3. TUI: separate AMM and RFQ columns (DESIGN §1.2 / §2.2) — RFQ column is live quote when
   `mode=pollable_quote`.

## Sources

- Fluxion trade-skill (public): https://github.com/Fluxion-Exchange/Fluxion-trade-skill
  (`references/contracts.md`, `references/xstock-rfq.md`)
- xStocks Assets API: `https://api.backed.fi/api/v1/token` (Mantle deployments + wrappers)
- Bybit instruments-info: `GET /v5/market/instruments-info?category=spot` field
  `xstockMultiplier` / `symbolType=xstocks`
