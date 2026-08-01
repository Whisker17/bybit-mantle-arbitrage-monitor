"""Seam: load_attribution_config + AttributionConfig validation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from monitor.attribution import (
    AttributionConfigError,
    default_attribution_path,
    load_attribution_config,
)


def test_default_attribution_path_points_at_repo_config() -> None:
    path = default_attribution_path()
    assert path.name == "attribution.yaml"
    assert path.is_file()


def test_load_checked_in_attribution_config() -> None:
    cfg = load_attribution_config()
    assert cfg.version == 1
    assert cfg.top_takers_n == 10
    assert cfg.address_code_batch_size == 50
    assert cfg.arb_bot.min_trades == 20
    assert cfg.arb_bot.min_convergence_ratio == 0.80
    assert cfg.arb_bot.min_bybit_align_ratio == 0.0
    assert cfg.price_keeper.min_trades == 10
    assert cfg.price_keeper.min_direction_share == 0.25
    assert cfg.price_keeper.max_median_notional_usd == Decimal(500)
    assert cfg.price_keeper.max_trade_notional_usd == Decimal(2000)
    assert cfg.retail.min_trades == 5
    assert cfg.activity.min_closed_share_for_all_hours == 0.10
    assert cfg.activity.min_trades_for_regime == 10
    assert cfg.bybit_correlation.lookback_ms == 5000
    assert cfg.bybit_correlation.min_move_bps == Decimal("1.0")


def test_reject_median_above_max_trade(tmp_path: Path) -> None:
    p = tmp_path / "attribution.yaml"
    p.write_text(
        dedent(
            """\
            version: 1
            top_takers_n: 10
            address_code_batch_size: 50
            arb_bot:
              min_trades: 20
              min_convergence_ratio: 0.8
            price_keeper:
              min_trades: 10
              min_direction_share: 0.25
              max_median_notional_usd: 3000
              max_trade_notional_usd: 2000
            retail:
              min_trades: 5
            activity:
              min_closed_share_for_all_hours: 0.1
              min_trades_for_regime: 10
            bybit_correlation:
              lookback_ms: 5000
              min_move_bps: 1.0
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(AttributionConfigError, match="max_median_notional_usd"):
        load_attribution_config(p)


def test_reject_convergence_ratio_out_of_range(tmp_path: Path) -> None:
    p = tmp_path / "attribution.yaml"
    p.write_text(
        dedent(
            """\
            version: 1
            top_takers_n: 10
            address_code_batch_size: 50
            arb_bot:
              min_trades: 20
              min_convergence_ratio: 1.5
            price_keeper:
              min_trades: 10
              min_direction_share: 0.25
              max_median_notional_usd: 500
              max_trade_notional_usd: 2000
            retail:
              min_trades: 5
            activity:
              min_closed_share_for_all_hours: 0.1
              min_trades_for_regime: 10
            bybit_correlation:
              lookback_ms: 5000
              min_move_bps: 1.0
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(AttributionConfigError):
        load_attribution_config(p)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AttributionConfigError, match="not found"):
        load_attribution_config(tmp_path / "nope.yaml")
