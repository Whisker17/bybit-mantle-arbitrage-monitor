"""Seams: load_pairs_config, de_multiplied_price, PairsConfig validation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from monitor.symbols import (
    PairsConfigError,
    RfqMode,
    de_multiplied_price,
    default_pairs_path,
    load_pairs_config,
    multiplier_map,
    multiplier_map_by_pair_id,
)

# Fixed inventory constants from the M1 research pass (2026-07-31).
EXPECTED_OVERLAP_IDS = {
    "AAPLx",
    "AMZNx",
    "COINx",
    "CRCLx",
    "GOOGLx",
    "HOODx",
    "MCDx",
    "METAx",
    "NVDAx",
    "SPCXx",
    "TSLAx",
}
# Liquid AMM pairs observed above the $50k Fluxion LP gate.
EXPECTED_LIQUID_AMM_IDS = {
    "AAPLx",
    "CRCLx",
    "GOOGLx",
    "HOODx",
    "METAx",
    "NVDAx",
    "TSLAx",
}


def test_default_pairs_path_points_at_repo_config() -> None:
    path = default_pairs_path()
    assert path.name == "bybit-fluxion.yaml"
    assert path.parent.name == "markets"
    assert path.is_file()


def test_load_checked_in_pairs_config() -> None:
    cfg = load_pairs_config()
    assert cfg.version == 1
    assert cfg.contracts.chain_id == 5000
    assert cfg.rfq.mode is RfqMode.POLLABLE_QUOTE
    assert cfg.rfq.request_type == "EXACT_INPUT"
    assert cfg.rfq.quote_asset == "USDC"

    ids = {p.id for p in cfg.pairs}
    assert ids == EXPECTED_OVERLAP_IDS

    liquid = {p.id for p in cfg.liquid_pairs()}
    assert liquid == EXPECTED_LIQUID_AMM_IDS

    with_amm = {p.id for p in cfg.pairs_with_amm()}
    assert EXPECTED_LIQUID_AMM_IDS <= with_amm
    assert "SPCXx" in with_amm  # low-liq but still has a pool
    assert "COINx" not in with_amm
    assert "AMZNx" not in with_amm
    assert "MCDx" not in with_amm

    tsla = cfg.pair_by_id("TSLAx")
    assert tsla.bybit.symbol == "TSLAXUSDT"
    assert tsla.bybit.multiplier == Decimal("1")
    assert tsla.fluxion.amm is not None
    assert tsla.fluxion.amm.kind == "v3"
    assert tsla.fluxion.amm.fee == 3000
    assert tsla.fluxion.quote_token == "USDC"

    aapl = cfg.pair_by_id("AAPLx")
    assert aapl.bybit.multiplier == Decimal("1.0026642075893797")
    assert aapl.low_liquidity is False

    spcx = cfg.pair_by_id("SPCXx")
    assert spcx.low_liquidity is True


def test_required_pair_fields_present() -> None:
    cfg = load_pairs_config()
    for pair in cfg.pairs:
        assert pair.bybit.symbol.endswith("USDT")
        assert pair.bybit.base_coin
        assert pair.bybit.multiplier > 0
        assert pair.fluxion.native_token.startswith("0x")
        assert len(pair.fluxion.native_token) == 42
        assert pair.fluxion.wrapper_token.startswith("0x")
        assert pair.fluxion.native_decimals == 18
        if pair.fluxion.amm is not None:
            assert pair.fluxion.amm.pool.startswith("0x")
            assert pair.fluxion.amm.fee > 0


def test_de_multiplied_price_divides_by_multiplier() -> None:
    # Independent worked example: token mid constructed so mid / mult = 100 exactly.
    mult = Decimal("1.0026642075893797")
    price = mult * Decimal("100")
    assert de_multiplied_price(price, mult) == Decimal("100")


def test_de_multiplied_price_rejects_non_positive_multiplier() -> None:
    with pytest.raises(ValueError, match="multiplier"):
        de_multiplied_price("100", "0")


def test_multiplier_map_from_config() -> None:
    cfg = load_pairs_config()
    by_symbol = multiplier_map(cfg)
    assert by_symbol["TSLAXUSDT"] == Decimal("1")
    assert by_symbol["AAPLXUSDT"] == Decimal("1.0026642075893797")
    assert set(by_symbol) == {p.bybit.symbol for p in cfg.pairs}

    by_id = multiplier_map_by_pair_id(cfg)
    assert by_id["TSLAx"] == Decimal("1")
    assert set(by_id) == EXPECTED_OVERLAP_IDS


_MINIMAL_CONTRACTS = dedent(
    """\
    contracts:
      chain_id: 5000
      fluxion_v3_factory: "0xF883162Ed9c7E8EF604214c964c678E40c9B737C"
      fluxion_v3_quoter: "0x3E4eE18Ac7280813236a1EB850679Da5322E14CE"
      fluxion_v3_router: "0x5628a59dF0ECAC3f3171f877A94bEb26BA6DFAa0"
      limit_order_protocol: "0x11de6011345586785810e52448a44c6595eedc18"
      usdc: "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"
      usdt0: "0x779Ded0c9e1022225f8E0630b35a9b54bE713736"
    rfq:
      mode: pollable_quote
      quote_url: "https://example.test/quote"
      proxy_quote_url: "https://example.test/proxy"
      request_type: EXACT_INPUT
      quote_asset: USDC
      rate_limit_per_minute: 60
      min_poll_interval_s: 1
      settlement: limit_order_protocol
    """
)


def _base_coin(pair_id: str) -> str:
    if pair_id.endswith("x"):
        return pair_id[:-1].upper() + "X"
    return pair_id.upper()


def _pair_yaml(
    *,
    pair_id: str = "TSLAx",
    symbol: str = "TSLAXUSDT",
    native: str = "0x8ad3c73f833d3f9a523ab01476625f269aeb7cf0",
    wrapper: str = "0x43680abf18cf54898be84c6ef78237cfbd441883",
    quote_token_address: str = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9",
    low_liquidity: bool = True,
    amm_block: str = "amm: null",
) -> str:
    base = _base_coin(pair_id)
    return dedent(
        f"""\
        - id: {pair_id}
          name: test
          low_liquidity: {str(low_liquidity).lower()}
          bybit:
            symbol: {symbol}
            base_coin: {base}
            multiplier: "1"
            multiplier_source: test
          fluxion:
            native_token: "{native}"
            native_decimals: 18
            quote_token: USDC
            quote_token_address: "{quote_token_address}"
            wrapper_token: "{wrapper}"
            {amm_block}
        """
    )


def _write_pairs_yaml(tmp_path: Path, pairs_body: str) -> Path:
    path = tmp_path / "pairs.yaml"
    path.write_text(
        "version: 1\ninventory_as_of: \"2026-07-31\"\nlow_liquidity_threshold_usd: 50000\n"
        + _MINIMAL_CONTRACTS
        + "pairs:\n"
        + pairs_body,
        encoding="utf-8",
    )
    return path


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _write_pairs_yaml(
        tmp_path,
        _pair_yaml()
        + _pair_yaml(
            pair_id="TSLAx",
            symbol="TSLAXUSDT2",
            native="0x1111111111111111111111111111111111111111",
            wrapper="0x2222222222222222222222222222222222222222",
        ),
    )
    with pytest.raises(PairsConfigError, match="unique"):
        load_pairs_config(path)


def test_load_rejects_low_liquidity_mismatch(tmp_path: Path) -> None:
    # No AMM ⇒ must be low_liquidity=true; false is invalid.
    path = _write_pairs_yaml(
        tmp_path,
        _pair_yaml(low_liquidity=False, amm_block="amm: null"),
    )
    with pytest.raises(PairsConfigError, match="low_liquidity"):
        load_pairs_config(path)


def test_load_rejects_quote_address_mismatch(tmp_path: Path) -> None:
    path = _write_pairs_yaml(
        tmp_path,
        _pair_yaml(quote_token_address="0x0000000000000000000000000000000000000001"),
    )
    with pytest.raises(PairsConfigError, match="quote_token_address"):
        load_pairs_config(path)


def test_load_rejects_non_mapping_root(tmp_path: Path) -> None:
    path = tmp_path / "pairs.yaml"
    path.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(PairsConfigError, match="mapping"):
        load_pairs_config(path)


def test_load_missing_file_is_clear(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(PairsConfigError, match="not found"):
        load_pairs_config(missing)
