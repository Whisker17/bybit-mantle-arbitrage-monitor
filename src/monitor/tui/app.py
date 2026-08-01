"""Textual live panel: overview table + pair detail (M5 / WHI-734)."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from monitor.attribution.config import load_attribution_config
from monitor.metrics.config import load_metrics_config
from monitor.metrics.session import SessionKind
from monitor.symbols import load_pairs_config
from monitor.tui.builder import build_overview, build_pair_detail
from monitor.tui.config import SortKey, TuiConfig, load_tui_config
from monitor.tui.format import (
    fmt_bps,
    fmt_direction,
    fmt_notional,
    fmt_price,
    fmt_session,
    fmt_signed_bps,
    short_addr,
    sparkline,
)
from monitor.tui.model import (
    OverviewModel,
    PairDetailModel,
    RunningEdgeState,
)
from monitor.tui.reader import JournalReader

_SORT_CYCLE: list[SortKey] = [
    "net_edge",
    "amm_spread",
    "rfq_spread",
    "volume_24h",
    "trades_24h",
    "pair_id",
    "bybit_mid",
]


class OverviewScreen(Screen[None]):
    """Primary page: one row per pair."""

    BINDINGS = [
        Binding("enter", "open_detail", "Detail", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, app_state: TuiApp) -> None:
        super().__init__()
        self._app_state = app_state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status", classes="status")
        yield DataTable(id="pairs", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#pairs", DataTable)
        table.add_columns(
            "Pair",
            "Sess",
            "Bid",
            "Ask",
            "Mid",
            "AMM",
            "RFQb",
            "RFQs",
            "AMM bps",
            "RFQ bps",
            "Net",
            "Dir",
            "Ven",
            "Vol24h",
            "N24h",
        )
        table.focus()
        self._render_model(self._app_state.overview)
        self.set_interval(
            self._app_state.tui.refresh_interval_s, self._app_state.tick_overview
        )

    def _render_model(self, model: OverviewModel | None) -> None:
        status = self.query_one("#status", Static)
        table = self.query_one("#pairs", DataTable)
        if model is None:
            status.update("[red]No data yet — waiting for collector journal…[/]")
            return
        arrow = "↓" if model.sort_desc else "↑"
        status.update(
            f"session={fmt_session(model.session_now)}  "
            f"sort={model.sort_key}{arrow}  "
            f"ref=${model.reference_size_usd:g}  "
            f"db={model.db_path}  "
            f"pairs={len(model.rows)}"
            + (f"  [red]{model.error}[/]" if model.error else "")
        )
        # Preserve cursor row by pair_id when possible.
        prev_pair: str | None = None
        if table.row_count > 0 and table.cursor_row is not None:
            try:
                row_key = table.get_row_at(table.cursor_row)
                if row_key:
                    prev_pair = str(row_key[0]).strip().lstrip("·").strip()
            except Exception:
                prev_pair = None
        table.clear()
        target_index: int | None = None
        for i, row in enumerate(model.rows):
            label = row.pair_id if not row.low_liquidity else f"·{row.pair_id}"
            cells = (
                label,
                fmt_session(row.session),
                fmt_price(row.bybit_bid),
                fmt_price(row.bybit_ask),
                fmt_price(row.bybit_mid),
                fmt_price(row.amm_mid),
                fmt_price(row.rfq_buy),
                fmt_price(row.rfq_sell),
                fmt_signed_bps(row.amm_spread_bps),
                fmt_signed_bps(row.rfq_spread_bps),
                fmt_signed_bps(row.net_edge_bps),
                fmt_direction(row.net_edge_direction),
                row.net_edge_venue or "—",
                fmt_notional(row.volume_24h),
                str(row.trades_24h),
            )
            key = table.add_row(*cells, key=row.pair_id)
            if row.low_liquidity:
                table.get_row(key)  # ensure materialised
            if prev_pair and row.pair_id == prev_pair:
                target_index = i
        if target_index is not None:
            table.move_cursor(row=target_index)

    def action_refresh(self) -> None:
        self._app_state.tick_overview()

    def action_cycle_sort(self) -> None:
        cur = self._app_state.sort_key
        try:
            idx = _SORT_CYCLE.index(cur)
        except ValueError:
            idx = -1
        self._app_state.sort_key = _SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)]
        self._app_state.tick_overview()

    def action_open_detail(self) -> None:
        table = self.query_one("#pairs", DataTable)
        if table.row_count == 0:
            return
        # Prefer coordinate row key.
        if table.coordinate_to_cell_key(table.cursor_coordinate).row_key is not None:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            pair_id = str(row_key.value)
        else:
            return
        self._app_state.open_detail(pair_id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pair_id = str(event.row_key.value)
        self._app_state.open_detail(pair_id)


class DetailScreen(Screen[None]):
    """Secondary page: spreads, trades, edge stats, attribution."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, app_state: TuiApp, pair_id: str) -> None:
        super().__init__()
        self._app_state = app_state
        self.pair_id = pair_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="detail_header", classes="status")
        with VerticalScroll():
            yield Static(id="spark", classes="panel")
            with Horizontal(classes="panels"):
                yield Static(id="edge_panel", classes="panel half")
                yield Static(id="attr_panel", classes="panel half")
            yield Static(id="trades_title", classes="panel-title")
            yield DataTable(id="trades", zebra_stripes=True, cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#trades", DataTable)
        table.add_columns(
            "Time",
            "Mech",
            "Dir",
            "Notional",
            "Px",
            "Bybit",
            "Conv",
            "Taker",
            "Label",
            "Tx",
        )
        self._app_state.tick_detail(self.pair_id)
        self.set_interval(
            self._app_state.tui.refresh_interval_s,
            lambda: self._app_state.tick_detail(self.pair_id),
        )

    def render_detail(self, model: PairDetailModel) -> None:
        hdr = self.query_one("#detail_header", Static)
        liq = " [dim](low liq)[/]" if model.low_liquidity else ""
        o = model.overview
        hdr.update(
            f"[bold]{model.pair_id}[/] {model.name}{liq}  "
            f"session={fmt_session(model.session_now)}  "
            f"mid={fmt_price(o.bybit_mid)}  "
            f"AMM={fmt_price(o.amm_mid)}  "
            f"net={fmt_signed_bps(o.net_edge_bps)} bps "
            f"({fmt_direction(o.net_edge_direction)} {o.net_edge_venue or '—'})"
        )

        spark_cells = sparkline(
            model.spread_series, width=self._app_state.tui.sparkline_width
        )
        spark_text = Text("AMM spread  ")
        for ch, sess in spark_cells:
            style = "green" if sess is SessionKind.OPEN else "yellow"
            if ch == " ":
                spark_text.append(ch)
            else:
                spark_text.append(ch, style=style)
        spark_text.append(
            f"  n={len(model.spread_series)}  "
            f"(green=open yellow=closed)"
        )
        self.query_one("#spark", Static).update(spark_text)

        self.query_one("#edge_panel", Static).update(_format_edge_panels(model))
        self.query_one("#attr_panel", Static).update(_format_attr_panel(model))
        self.query_one("#trades_title", Static).update(
            f"Fluxion fills (latest {len(model.trades)})"
        )

        table = self.query_one("#trades", DataTable)
        table.clear()
        for t in model.trades:
            conv = (
                "—"
                if t.converging is None
                else ("yes" if t.converging else "no")
            )
            table.add_row(
                _fmt_ts(t.ts_ms),
                t.mechanism,
                t.direction,
                fmt_notional(t.notional_usd),
                fmt_price(t.price),
                fmt_price(t.bybit_mid),
                conv,
                short_addr(t.taker),
                t.taker_label or "—",
                short_addr(t.tx_hash, head=4, tail=4),
            )

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._app_state.tick_detail(self.pair_id)


def _fmt_ts(ts_ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%H:%M:%S")


def _dist_line(label: str, dist: object) -> str:
    from monitor.metrics.stats import Distribution

    assert isinstance(dist, Distribution)
    if dist.count == 0:
        return f"{label}: n=0"
    return (
        f"{label}: n={dist.count}  "
        f"p50={fmt_bps(dist.p50)}  "
        f"p95={fmt_bps(dist.p95)}  "
        f"p99={fmt_bps(dist.p99)}  "
        f"max={fmt_bps(dist.max)}"
    )


def _format_edge_panels(model: PairDetailModel) -> str:
    lines = ["[bold]Net paper edge[/] (M3)", ""]
    for title, panel in (("AMM", model.edge_amm), ("RFQ", model.edge_rfq)):
        cur = panel.current
        if cur is None:
            lines.append(f"{title}: —")
        else:
            lines.append(
                f"{title}: {fmt_signed_bps(cur.net_edge_bps)} bps  "
                f"{fmt_direction(cur.direction)}  "
                f"@ ${cur.size_usd:g}  "
                f"gross={fmt_signed_bps(cur.gross_spread_bps)}"
            )
            c = cur.costs
            lines.append(
                f"  wear: bybit={fmt_bps(c.bybit_taker_bps)} "
                f"fee={fmt_bps(c.fluxion_fee_bps)} "
                f"b_slip={fmt_bps(c.bybit_slip_bps)} "
                f"f_slip={fmt_bps(c.fluxion_slip_bps)} "
                f"gas={fmt_bps(c.gas_bps)} "
                f"basis={fmt_bps(c.basis_bps)} "
                f"total={fmt_bps(c.total_wear_bps)}"
            )
        lines.append(f"  {_dist_line('all', panel.distribution_all)}")
        lines.append(f"  {_dist_line('open', panel.distribution_open)}")
        lines.append(f"  {_dist_line('closed', panel.distribution_closed)}")
        b = panel.breach_all
        lines.append(
            f"  breaches(all): episodes={b.episode_count} "
            f"dur_ms={b.total_duration_ms} "
            f"now={'Y' if b.currently_breaching else 'N'}"
        )
        lines.append("")
    return "\n".join(lines)


def _format_attr_panel(model: PairDetailModel) -> str:
    lines = ["[bold]Attribution[/] (M4)", ""]
    attr = model.attribution
    if attr is None:
        lines.append("no trades in window")
        return "\n".join(lines)
    m = attr.mechanism
    lines.append(
        f"mechanism: AMM={m.amm_trades} RFQ={m.rfq_trades} "
        f"rfq_share={_pct(model.rfq_mechanism_share)}"
    )
    lines.append(
        f"convergence_share={_pct(attr.convergence_share)} "
        f"(scored={attr.n_convergence_scored})"
    )
    lines.append(
        f"arb_bot={_pct(model.arb_bot_trade_share)}  "
        f"price_keeper={_pct(model.price_keeper_trade_share)}"
    )
    lines.append("")
    lines.append("top takers:")
    if not attr.top_takers:
        lines.append("  (none)")
    for t in attr.top_takers[:10]:
        f = t.features
        lines.append(
            f"  {short_addr(f.address)}  {t.label.value:12}  "
            f"n={f.n_trades}  notional={fmt_notional(f.notional_usd)}  "
            f"conv={_pct(f.convergence_ratio)}  "
            f"{'C' if f.is_contract else ('E' if f.is_contract is False else '?')}"
        )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


class TuiApp(App[None]):
    """Root application holding configs + running edge state."""

    CSS = """
    .status { height: 1; padding: 0 1; background: $surface; }
    .panel { padding: 0 1; margin: 0 0 1 0; border: solid $primary-darken-2; }
    .panel-title { padding: 0 1; height: 1; }
    .half { width: 1fr; height: auto; min-height: 12; }
    .panels { height: auto; }
    DataTable { height: 1fr; }
    #trades { height: 16; }
    """

    TITLE = "Bybit ⇄ Fluxion xStocks"
    SUB_TITLE = "live paper-arb panel (M5)"

    def __init__(
        self,
        *,
        tui: TuiConfig,
        pairs_path: Path | None = None,
        metrics_path: Path | None = None,
        attribution_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.tui = tui
        self.pairs = load_pairs_config(pairs_path)
        self.metrics = load_metrics_config(metrics_path)
        self.attribution = load_attribution_config(attribution_path)
        self.db_path = db_path if db_path is not None else tui.resolved_sqlite_path()
        # Fail fast if reference size is not on the metrics ladder.
        if self.tui.reference_size_usd not in self.metrics.size_ladder_usd:
            raise SystemExit(
                f"tui.reference_size_usd={self.tui.reference_size_usd} must be one of "
                f"metrics.size_ladder_usd={self.metrics.size_ladder_usd}"
            )
        self.sort_key: SortKey = tui.default_sort
        self.sort_desc: bool = tui.default_sort_desc
        self.edge_state = RunningEdgeState()
        self.overview: OverviewModel | None = None
        self._detail_pair: str | None = None

    def on_mount(self) -> None:
        self.push_screen(OverviewScreen(self))
        self.tick_overview()

    def open_detail(self, pair_id: str) -> None:
        self._detail_pair = pair_id
        self.push_screen(DetailScreen(self, pair_id))

    def tick_overview(self) -> None:
        try:
            if not self.db_path.is_file():
                self.overview = OverviewModel(
                    generated_ts_ms=0,
                    session_now=SessionKind.CLOSED,
                    sort_key=self.sort_key,
                    sort_desc=self.sort_desc,
                    reference_size_usd=self.tui.reference_size_usd,
                    rows=[],
                    db_path=str(self.db_path),
                    error=f"journal missing: {self.db_path}",
                )
            else:
                with JournalReader(self.db_path) as reader:
                    self.overview = build_overview(
                        pairs=self.pairs,
                        reader=reader,
                        metrics=self.metrics,
                        tui=self.tui,
                        sort_key=self.sort_key,
                        sort_desc=self.sort_desc,
                    )
        except Exception as exc:  # keep panel alive
            self.overview = OverviewModel(
                generated_ts_ms=0,
                session_now=SessionKind.CLOSED,
                sort_key=self.sort_key,
                sort_desc=self.sort_desc,
                reference_size_usd=self.tui.reference_size_usd,
                rows=[],
                db_path=str(self.db_path),
                error=str(exc),
            )
        # Refresh overview screen if active.
        screen = self.screen
        if isinstance(screen, OverviewScreen):
            screen._render_model(self.overview)

    def tick_detail(self, pair_id: str) -> None:
        screen = self.screen
        if not isinstance(screen, DetailScreen):
            return
        try:
            pair = self.pairs.pair_by_id(pair_id)
            with JournalReader(self.db_path) as reader:
                model = build_pair_detail(
                    pair=pair,
                    reader=reader,
                    metrics=self.metrics,
                    attribution_cfg=self.attribution,
                    tui=self.tui,
                    edge_state=self.edge_state,
                )
            screen.render_detail(model)
        except Exception as exc:
            screen.query_one("#detail_header", Static).update(f"[red]{exc}[/]")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m monitor.tui",
        description="Bybit ⇄ Fluxion xStocks live TUI panel (M5 / WHI-734).",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config/tui.yaml (default: repo config/tui.yaml)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override collector SQLite path (default: tui.sqlite_path)",
    )
    p.add_argument(
        "--pairs",
        type=Path,
        default=None,
        help="Override config/pairs.yaml",
    )
    p.add_argument(
        "--metrics",
        type=Path,
        default=None,
        help="Override config/metrics.yaml",
    )
    p.add_argument(
        "--attribution",
        type=Path,
        default=None,
        help="Override config/attribution.yaml",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    tui = load_tui_config(args.config)
    app = TuiApp(
        tui=tui,
        pairs_path=args.pairs,
        metrics_path=args.metrics,
        attribution_path=args.attribution,
        db_path=args.db,
    )
    app.run()


if __name__ == "__main__":
    main()
