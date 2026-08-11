"""Seam: load_metrics_config + MetricsConfig validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from monitor.metrics import MetricsConfigError, default_metrics_path, load_metrics_config


def test_default_metrics_path_points_at_repo_config() -> None:
    path = default_metrics_path()
    assert path.name == "metrics.yaml"
    assert path.is_file()


def test_load_checked_in_metrics_config() -> None:
    from decimal import Decimal

    cfg = load_metrics_config()
    assert cfg.version == 1
    assert cfg.size_ladder_usd == [Decimal(1000), Decimal(5000), Decimal(20000)]
    assert cfg.bybit_taker_fee_bps == Decimal(15)
    # Base metrics.yaml stays 0; bybit-fluxion market file overrides to 7.5.
    assert cfg.usdt_usdc_basis_bps == Decimal(0)
    assert cfg.gas_usd_per_swap == Decimal("0.01")
    assert cfg.breach_size_usd == Decimal(1000)
    assert cfg.max_breach_gap_ms == 300_000
    assert cfg.session.timezone == "America/New_York"
    assert cfg.session.open == "09:30"
    assert cfg.session.close == "16:00"
    assert cfg.session.early_close == "13:00"
    assert cfg.pnl_v2 is not None
    assert cfg.pnl_v2.buckets_usd[0] == Decimal(10)
    assert cfg.pnl_v2.buckets_usd[-1] == Decimal(10000)
    assert cfg.pnl_v2.coarse_points == 24
    # WHI-962: bot floor 1.5 USDT + sequential bar k=1.5.
    assert cfg.pnl_v2.min_profit_usd == Decimal("1.5")
    assert cfg.pnl_v2.drift_premium_k == Decimal("1.5")


def test_metrics_config_accepts_negative_basis() -> None:
    """WHI-960: signed USDC premium may be negative (USDT richer)."""
    from decimal import Decimal

    from monitor.markets.models import MarketCosts
    from monitor.metrics.config import MetricsConfig, PnlV2Config, SessionConfig

    cfg = MetricsConfig(
        version=1,
        size_ladder_usd=[Decimal(1000)],
        bybit_taker_fee_bps=Decimal(15),
        usdt_usdc_basis_bps=Decimal("-3.5"),
        gas_usd_per_swap=Decimal("0.01"),
        session=SessionConfig(
            timezone="America/New_York",
            open="09:30",
            close="16:00",
            early_close="13:00",
        ),
        breach_size_usd=Decimal(1000),
        max_breach_gap_ms=300_000,
        pnl_v2=PnlV2Config.model_validate(
            {
                "buckets_usd": [10, 50, 100, 500, 1000, 10000],
                "q_min_usd": 10,
                "config_cap_usd": 10000,
            }
        ),
    )
    assert cfg.usdt_usdc_basis_bps == Decimal("-3.5")
    # Market file path also accepts a signed premium (YAML → MarketCosts).
    costs = MarketCosts.model_validate(
        {
            "cex_taker_fee_bps": 15,
            "gas_usd_per_swap": "0.01",
            "quote_basis_bps": "-3.5",
        }
    )
    assert costs.quote_basis_bps == Decimal("-3.5")


def test_reject_breach_size_not_on_ladder(tmp_path: Path) -> None:
    p = tmp_path / "metrics.yaml"
    p.write_text(
        dedent(
            """\
            version: 1
            size_ladder_usd: [1000, 5000]
            bybit_taker_fee_bps: 10
            gas_usd_per_swap: 0.01
            breach_size_usd: 2000
            session:
              timezone: America/New_York
              open: "09:30"
              close: "16:00"
              early_close: "13:00"
            pnl_v2:
              buckets_usd: [10, 50, 100, 500, 1000, 10000]
              q_min_usd: 10
              config_cap_usd: 10000
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(MetricsConfigError, match="breach_size_usd"):
        load_metrics_config(p)


def test_reject_non_ascending_ladder(tmp_path: Path) -> None:
    p = tmp_path / "metrics.yaml"
    p.write_text(
        dedent(
            """\
            version: 1
            size_ladder_usd: [5000, 1000]
            bybit_taker_fee_bps: 10
            gas_usd_per_swap: 0.01
            breach_size_usd: 5000
            session:
              timezone: America/New_York
              open: "09:30"
              close: "16:00"
              early_close: "13:00"
            pnl_v2:
              buckets_usd: [10, 50, 100, 500, 1000, 10000]
              q_min_usd: 10
              config_cap_usd: 10000
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(MetricsConfigError, match="ascending"):
        load_metrics_config(p)


def test_reject_missing_pnl_v2(tmp_path: Path) -> None:
    p = tmp_path / "metrics.yaml"
    p.write_text(
        dedent(
            """\
            version: 1
            size_ladder_usd: [1000, 5000]
            bybit_taker_fee_bps: 10
            gas_usd_per_swap: 0.01
            breach_size_usd: 1000
            session:
              timezone: America/New_York
              open: "09:30"
              close: "16:00"
              early_close: "13:00"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(MetricsConfigError, match="pnl_v2"):
        load_metrics_config(p)
