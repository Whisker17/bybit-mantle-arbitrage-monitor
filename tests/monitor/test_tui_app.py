"""Seam: TUI app boot — screens must mount and render against a real journal.

Regression for WHI-750: TuiApp.on_mount used to tick *after* push_screen, so
render_model wrote rows into a DataTable whose on_mount (add_columns) had not
run yet — interactive startup crashed with "More values provided than there
are columns" on any non-empty pairs config.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from textual.widgets import DataTable

from monitor.quotes import BybitBookTick
from monitor.storage import SqliteStore
from monitor.tui.app import OverviewScreen, TuiApp
from monitor.tui.config import load_tui_config

N_OVERVIEW_COLUMNS = 15


def _seed_journal(path: Path) -> None:
    tick = BybitBookTick(
        pair_id="AAPLx",
        symbol="AAPLXUSDT",
        exchange_ts_ms=1_785_576_000_000,
        recv_ts_ms=1_785_576_000_000,
        bid=Decimal("308.06"),
        ask=Decimal("308.67"),
        bid_de_multiplied=Decimal("307.24"),
        ask_de_multiplied=Decimal("307.85"),
        multiplier=Decimal("1.0026642075893797"),
    )
    with SqliteStore(path) as store:
        store.insert_bybit_book([tick])


@pytest.mark.asyncio
async def test_app_boots_and_renders_overview(tmp_path: Path) -> None:
    db = tmp_path / "journal.db"
    _seed_journal(db)
    app = TuiApp(tui=load_tui_config(), db_path=db)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, OverviewScreen)
        table = app.screen.query_one("#pairs", DataTable)
        assert len(table.columns) == N_OVERVIEW_COLUMNS
        # Every configured pair gets a row; the seeded one carries prices.
        assert table.row_count == len(app.pairs.pairs)
        assert app.overview is not None and app.overview.error is None


@pytest.mark.asyncio
async def test_app_boots_without_journal_file(tmp_path: Path) -> None:
    app = TuiApp(tui=load_tui_config(), db_path=tmp_path / "missing.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, OverviewScreen)
        assert app.overview is not None
        assert app.overview.error is not None  # surfaced, not crashed
