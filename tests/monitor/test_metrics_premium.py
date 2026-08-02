"""WHI-779: tokenized mid vs underlying equity premium (bps)."""

from __future__ import annotations

from decimal import Decimal

from monitor.metrics.premium import (
    PremiumSnapshot,
    build_premium_snapshot,
    mean_mid,
    premium_bps,
    premium_type_label,
)
from monitor.metrics.stats import Distribution
from monitor.quotes import UnderlyingPriceTick


def test_premium_bps_hand_calc() -> None:
    """Acceptance: page number = (tokenized / underlying − 1) × 10⁴."""
    # 305 / 300 − 1 = 0.016666… → 166.666… bps
    got = premium_bps(Decimal("305"), Decimal("300"))
    assert got is not None
    assert abs(got - Decimal("166.6666666666666666666666667")) < Decimal("0.0001")
    # Equal prices → 0 bps
    assert premium_bps(Decimal("100"), Decimal("100")) == Decimal(0)
    # Tokenized cheaper → negative premium
    assert premium_bps(Decimal("99"), Decimal("100")) == Decimal("-100")


def test_premium_bps_guards() -> None:
    assert premium_bps(None, Decimal("100")) is None
    assert premium_bps(Decimal("100"), None) is None
    assert premium_bps(Decimal("100"), Decimal("0")) is None
    assert premium_bps(Decimal("0"), Decimal("100")) is None
    assert premium_bps(Decimal("-1"), Decimal("100")) is None


def test_mean_mid_available_sides() -> None:
    assert mean_mid(Decimal("10"), Decimal("12")) == Decimal("11")
    assert mean_mid(Decimal("10"), None) == Decimal("10")
    assert mean_mid(None, Decimal("12")) == Decimal("12")
    assert mean_mid(None, None) is None


def test_premium_type_label_closed_session() -> None:
    assert premium_type_label("live") == "live"
    assert premium_type_label("close") == "vs close"
    assert premium_type_label("pre") == "vs pre"
    assert premium_type_label("post") == "vs post"
    assert premium_type_label("stale") == "stale"
    assert premium_type_label(None) is None


def test_build_premium_snapshot_private() -> None:
    snap = build_premium_snapshot(
        ticker="SPCX",
        underlying=None,
        cex_mid=Decimal("100"),
        amm_mid=Decimal("101"),
        rfq_mid=None,
        private=True,
    )
    assert snap.empty_reason == "private"
    assert snap.price is None
    assert snap.premium_bps is None
    assert snap.cex_premium_bps is None


def test_build_premium_snapshot_no_data() -> None:
    snap = build_premium_snapshot(
        ticker="AAPL",
        underlying=None,
        cex_mid=Decimal("310"),
        amm_mid=None,
        rfq_mid=None,
        private=False,
    )
    assert snap.empty_reason == "no_data"
    assert snap.ticker == "AAPL"
    assert snap.premium_bps is None


def test_build_premium_snapshot_three_venues() -> None:
    tick = UnderlyingPriceTick(
        ticker="AAPL",
        price=Decimal("300"),
        currency="USD",
        price_type="close",
        as_of_ms=1_700_000_000_000,
        recv_ts_ms=1_700_000_000_050,
        source="pyth_hermes",
        feed_id="abc",
    )
    # CEX 303 → +100 bps; AMM 306 → +200 bps; RFQ 309 → +300 bps
    snap = build_premium_snapshot(
        ticker="AAPL",
        underlying=tick,
        cex_mid=Decimal("303"),
        amm_mid=Decimal("306"),
        rfq_mid=Decimal("309"),
        private=False,
    )
    assert isinstance(snap, PremiumSnapshot)
    assert snap.empty_reason is None
    assert snap.price == Decimal("300")
    assert snap.price_type == "close"
    assert snap.as_of_ms == 1_700_000_000_000
    assert snap.premium_bps == Decimal("100")  # CEX default
    assert snap.cex_premium_bps == Decimal("100")
    assert snap.amm_premium_bps == Decimal("200")
    assert snap.rfq_premium_bps == Decimal("300")
    assert snap.type_label == "vs close"


def test_premium_distribution_from_series() -> None:
    values = [Decimal("-10"), Decimal("0"), Decimal("20"), Decimal("40")]
    dist = Distribution.from_values(values)
    assert dist.count == 4
    assert dist.max == Decimal("40")
    assert dist.p50 is not None


def test_equity_equivalent_mid_bstocks_divides_ui_multiplier() -> None:
    from monitor.metrics.premium import equity_equivalent_mid

    # display*mult journal mid 300 with mult 100 → per-share 3
    assert equity_equivalent_mid(
        Decimal("300"), ui_multiplier=Decimal("100")
    ) == Decimal("3")
    # Bybit path: no ui_multiplier → pass through
    assert equity_equivalent_mid(Decimal("305"), ui_multiplier=None) == Decimal("305")


def test_reclassify_stale_when_as_of_old_during_open() -> None:
    """Read path: live stamp + old as_of during RTH → stale (spec closed-session)."""
    from datetime import UTC, datetime

    from monitor.metrics.premium import reclassify_underlying_for_display
    from monitor.underlying.config import load_underlying_config

    cfg = load_underlying_config()
    # 2026-06-03 (Wed) 18:00 UTC = 14:00 ET (EDT) — NYSE open; holiday table 2025–27.
    now_ms = int(datetime(2026, 6, 3, 18, 0, tzinfo=UTC).timestamp() * 1000)
    # as_of 10 minutes older than stale_after_open_ms (120s) → stale
    as_of = now_ms - 600_000
    tick = UnderlyingPriceTick(
        ticker="AAPL",
        price=Decimal("200"),
        currency="USD",
        price_type="live",
        as_of_ms=as_of,
        recv_ts_ms=as_of + 50,
        source="pyth_hermes",
    )
    out = reclassify_underlying_for_display(
        tick,
        now_ms=now_ms,
        session=cfg.session,
        stale_after_open_ms=cfg.stale_after_open_ms,
        stale_after_closed_ms=cfg.stale_after_closed_ms,
        stale_after_abs_ms=cfg.stale_after_abs_ms,
    )
    assert out.price_type == "stale"
    # Fresh as_of stays live
    fresh = reclassify_underlying_for_display(
        UnderlyingPriceTick(
            ticker="AAPL",
            price=Decimal("200"),
            currency="USD",
            price_type="live",
            as_of_ms=now_ms - 30_000,
            recv_ts_ms=now_ms,
            source="pyth_hermes",
        ),
        now_ms=now_ms,
        session=cfg.session,
        stale_after_open_ms=cfg.stale_after_open_ms,
        stale_after_closed_ms=cfg.stale_after_closed_ms,
        stale_after_abs_ms=cfg.stale_after_abs_ms,
    )
    assert fresh.price_type == "live"
