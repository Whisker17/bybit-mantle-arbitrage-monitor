"""Seam: sequential-execution drift bar (WHI-962)."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.drift import (
    annotate_drift,
    clears_drift,
    drift_premium_bps,
    drift_wire_for_pair,
    select_sigma_bps,
    session_key,
)
from monitor.metrics.session import SessionKind
from monitor.symbols import load_pairs_config
from monitor.symbols.models import SigmaTransitBps


def test_session_key_normalizes() -> None:
    assert session_key(SessionKind.OPEN) == "open"
    assert session_key(SessionKind.CLOSED) == "closed"
    assert session_key("open") == "open"
    assert session_key("CLOSED") == "closed"
    assert session_key(None) is None
    assert session_key("holiday") is None


def test_select_sigma_session_split() -> None:
    sigma = SigmaTransitBps(open=Decimal("68.83"), closed=Decimal("58.57"))
    assert select_sigma_bps(sigma, "open") == Decimal("68.83")
    assert select_sigma_bps(sigma, "closed") == Decimal("58.57")
    assert select_sigma_bps(None, "open") is None
    assert select_sigma_bps(sigma, None) is None


def test_drift_premium_and_clears() -> None:
    # CRCLx open: 1.5 × 68.83 = 103.245
    sigma = Decimal("68.83")
    prem = drift_premium_bps(sigma, Decimal("1.5"))
    assert prem == Decimal("103.245")
    assert clears_drift(Decimal("15"), prem) is False
    assert clears_drift(Decimal("110"), prem) is True
    assert clears_drift(None, prem) is None
    assert clears_drift(Decimal("10"), None) is None


def test_annotate_crclx_style_fails_drift() -> None:
    """Worked example from WHI-962: ~15 bps net vs 103 bps bar."""
    sigma = SigmaTransitBps(open=Decimal("68.83"), closed=Decimal("58.57"))
    ann = annotate_drift(
        sigma=sigma,
        session="open",
        k=Decimal("1.5"),
        optimal_direction="buy_fluxion_sell_bybit",
        optimal_net_bps=Decimal("15"),
    )
    assert ann.sigma_transit_bps == Decimal("68.83")
    assert ann.drift_premium_bps == Decimal("103.245")
    assert ann.clears_drift_optimal is False
    assert ann.clears_drift["buy_fluxion_sell_bybit"] is False
    wire = ann.to_dict()
    assert wire["sigma_transit_bps"] == "68.83"
    assert wire["drift_premium_bps"] == "103.245"
    assert wire["clears_drift_optimal"] is False


def test_inventory_sigma_matches_m8_delay_decay() -> None:
    """σ values in bybit-fluxion.yaml match docs/references/m8-delay-decay.md @10m."""
    # Source of record table (open / closed @ N=10m).
    expected = {
        "AAPLx": (Decimal("24.85"), Decimal("9.84")),
        "CRCLx": (Decimal("68.83"), Decimal("58.57")),
        "GOOGLx": (Decimal("74.27"), Decimal("22.18")),
        "HOODx": (Decimal("44.03"), Decimal("25.08")),
        "METAx": (Decimal("22.36"), Decimal("13.18")),
        "NVDAx": (Decimal("26.50"), Decimal("14.19")),
        "TSLAx": (Decimal("25.13"), Decimal("11.86")),
        "SPCXx": (Decimal("78.51"), Decimal("42.75")),
    }
    cfg = load_pairs_config()
    for pair_id, (open_bps, closed_bps) in expected.items():
        pair = cfg.pair_by_id(pair_id)
        assert pair.sigma_transit_bps is not None, pair_id
        assert pair.sigma_transit_bps.open == open_bps
        assert pair.sigma_transit_bps.closed == closed_bps
    # Unmeasured low-liq no-AMM pairs stay None.
    for pair_id in ("AMZNx", "COINx", "MCDx"):
        assert cfg.pair_by_id(pair_id).sigma_transit_bps is None


def test_drift_wire_for_pair_uses_inventory() -> None:
    pair = load_pairs_config().pair_by_id("CRCLx")
    wire = drift_wire_for_pair(
        pair=pair,
        session="open",
        k=Decimal("1.5"),
        optimal_direction="buy_fluxion_sell_bybit",
        optimal_net_bps=Decimal("12"),
    )
    assert wire["sigma_transit_bps"] == "68.83"
    assert wire["clears_drift_optimal"] is False
    assert wire["clears_drift"]["buy_fluxion_sell_bybit"] is False
