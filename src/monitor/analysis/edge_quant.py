"""Edge-window statistics and single-flight capturable profit (WHI-866 / WHI-909 / M8).

Pure aggregation. Callers feed samples already scored by the PnL v2 engine
(``compute_pnl_usd`` / RFQ poll rows). This module never reimplements venue
fees, slip, or gas — but it *does* host pure cost-stack helpers used by the
M0 re-run (live USDC premium, rebalance amortization, extended sweep grid).

Decision rule (bot DESIGN §1.4): go if average capturable profit ≥ 15 USDT/day
at 5,000 inventory with ≤ 1,000 per trade and ≥ 3 symbols with stable windows.

Capturable profit respects **single-flight** (at most one trade in flight) and
the per-trade notional rung. Multi-symbol portfolio mode does not double-count
overlapping windows beyond what one in-flight slot can take.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

VenueLabel = Literal["amm", "rfq"]

# Direction that builds inventory skew (Fluxion long / Bybit short) in the
# observed xStocks regime. Rebalance amortization is charged only here.
SKEW_BUILDING_DIRECTION = "buy_fluxion_sell_bybit"

BPS = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class EdgeSample:
    """One aligned journal sample with net cash-flow PnL already computed.

    ``edge_bps`` is ``pnl_usd / size_usd * 1e4`` when fillable; negative when
    the paper trade loses money. Unfillable samples must not appear here
    (caller filters them) — a zero-pnl unfillable would create fake windows.
    """

    ts_ms: int
    pair_id: str
    direction: str
    session: Literal["open", "closed"]
    venue: VenueLabel
    size_usd: Decimal
    edge_bps: Decimal
    pnl_usd: Decimal


@dataclass(frozen=True, slots=True)
class OpportunityWindow:
    """Contiguous run of samples where ``edge_bps >= min_edge_bps``."""

    pair_id: str
    direction: str
    session: Literal["open", "closed"]
    venue: VenueLabel
    size_usd: Decimal
    start_ms: int
    end_ms: int
    n_samples: int
    # Representative trade PnL for one entry: first in-threshold sample
    # (fire-on-open), not the in-window max — max would overstate capturable
    # profit when the bot cannot wait for the peak inside the window.
    trade_pnl_usd: Decimal
    # Peak edge observed inside the window (bps) — diagnostic only.
    peak_edge_bps: Decimal

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True, slots=True)
class SweepRow:
    """One threshold on the min_edge_bps sweep."""

    min_edge_bps: Decimal
    n_windows: int
    windows_per_day: float
    duration_median_ms: int
    duration_p90_ms: int
    capturable_profit_usd: Decimal
    capturable_profit_per_day: Decimal


@dataclass(frozen=True, slots=True)
class ThresholdFit:
    """Lowest threshold capturing ≥ ``capture_fraction`` of T=0 profit."""

    min_edge_bps: Decimal
    capture_fraction: Decimal
    profit_at_fit: Decimal
    profit_at_zero: Decimal
    windows_per_day: float
    profit_per_day: Decimal
    # False when profit_at_zero ≤ 0 (no positive edge to fit against).
    fitted: bool


def detect_windows(
    samples: Sequence[EdgeSample],
    *,
    min_edge_bps: Decimal,
    max_gap_ms: int,
) -> list[OpportunityWindow]:
    """Collapse consecutive in-threshold samples into opportunity windows.

    A new window starts when edge clears ``min_edge_bps`` after a below-
    threshold sample, or when the inter-sample gap exceeds ``max_gap_ms``
    (collector downtime / session break must not glue distant spikes).
    Samples must already be sorted by ``ts_ms`` ascending for one
    (pair, direction, venue, size, session) series — mixed keys raise.
    """
    if not samples:
        return []
    _assert_homogeneous(samples)

    windows: list[OpportunityWindow] = []
    open_start: int | None = None
    open_end: int = 0
    open_n = 0
    open_peak = Decimal(0)
    open_trade = Decimal(0)
    prev_ts: int | None = None

    def _close() -> None:
        nonlocal open_start, open_n, open_peak, open_trade
        if open_start is None or open_n == 0:
            open_start = None
            open_n = 0
            return
        s0 = samples[0]
        windows.append(
            OpportunityWindow(
                pair_id=s0.pair_id,
                direction=s0.direction,
                session=s0.session,
                venue=s0.venue,
                size_usd=s0.size_usd,
                start_ms=open_start,
                end_ms=open_end,
                n_samples=open_n,
                trade_pnl_usd=open_trade,
                peak_edge_bps=open_peak,
            )
        )
        open_start = None
        open_n = 0
        open_peak = Decimal(0)
        open_trade = Decimal(0)

    for s in samples:
        in_edge = s.edge_bps >= min_edge_bps and s.pnl_usd > 0
        gap_break = (
            prev_ts is not None
            and open_start is not None
            and (s.ts_ms - prev_ts) > max_gap_ms
        )
        if gap_break:
            _close()
        if in_edge:
            if open_start is None:
                open_start = s.ts_ms
                open_peak = s.edge_bps
                open_trade = s.pnl_usd  # fire-on-open
                open_n = 1
            else:
                open_n += 1
                if s.edge_bps > open_peak:
                    open_peak = s.edge_bps
            open_end = s.ts_ms
        else:
            _close()
        prev_ts = s.ts_ms
    _close()
    return windows


def capturable_profit_single_flight(
    windows: Sequence[OpportunityWindow],
    *,
    reentry_cooldown_ms: int,
    trade_duration_ms: int = 5_000,
    max_trade_usd: Decimal | None = None,
) -> Decimal:
    """Sum trade PnL under single-flight + one-entry-per-window (with re-entry).

    Model (bot DESIGN §2.4):
    - At most one trade in flight globally for this series.
    - Enter at window open (or at re-entry after cooldown while still inside
      a long window). Each entry books ``trade_pnl_usd`` once (notional is
      already baked into that figure by the engine).
    - Flight occupies ``trade_duration_ms``; next entry needs
      ``trade_duration_ms + reentry_cooldown_ms`` after the previous entry.
    - ``max_trade_usd`` is advisory only when the sample size already equals
      the rung; if a window's size exceeds the cap, scale PnL linearly
      (conservative: never invent a better fill at smaller size).
    """
    if not windows:
        return Decimal(0)
    ordered = sorted(windows, key=lambda w: (w.start_ms, w.end_ms))
    profit = Decimal(0)
    next_free_ms = 0
    for w in ordered:
        entry = max(w.start_ms, next_free_ms)
        # Re-entries while the window still has room after cooldown.
        # Inter-window single-flight only holds for trade_duration_ms; the
        # re-entry cooldown is *within* a window (issue: one trade per window
        # unless the window outlasts the cooldown).
        while entry <= w.end_ms:
            trade = w.trade_pnl_usd
            if max_trade_usd is not None and w.size_usd > max_trade_usd > 0:
                trade = trade * (max_trade_usd / w.size_usd)
            if trade > 0:
                profit += trade
            # Guard zero/negative durations so the loop always advances.
            step = max(1, trade_duration_ms + max(0, reentry_cooldown_ms))
            next_free_ms = entry + max(1, trade_duration_ms)
            entry = entry + step
            if entry > w.end_ms:
                break
    return profit


def portfolio_capturable_profit(
    windows: Sequence[OpportunityWindow],
    *,
    reentry_cooldown_ms: int = 0,
    trade_duration_ms: int = 5_000,
    max_trade_usd: Decimal = Decimal(1000),
    inventory_usd: Decimal = Decimal(5000),
) -> Decimal:
    """Single-flight across symbols: one entry per window, best-first in time.

    At each free slot, among remaining windows that still contain a feasible
    entry, pick the highest scaled ``trade_pnl_usd`` and **consume that window**
    (no within-window re-entry). Flight holds only ``trade_duration_ms``.
    ``reentry_cooldown_ms`` is accepted for call-site symmetry with the
    series-level helper but is unused here — portfolio mode is always one
    entry per window. ``inventory_usd`` caps per-trade size with
    ``max_trade_usd`` (single-flight ⇒ no parallel exposure).
    """
    _ = reentry_cooldown_ms  # intentional no-op; see docstring
    if not windows or inventory_usd <= 0 or max_trade_usd <= 0:
        return Decimal(0)
    cap = min(max_trade_usd, inventory_usd)
    scaled: list[tuple[int, int, Decimal]] = []
    for w in windows:
        trade = w.trade_pnl_usd
        if w.size_usd > cap > 0:
            trade = trade * (cap / w.size_usd)
        if trade <= 0:
            continue
        scaled.append((w.start_ms, w.end_ms, trade))
    if not scaled:
        return Decimal(0)

    profit = Decimal(0)
    next_free_ms = 0
    # Portfolio mode is one entry per window (no within-window re-entry):
    # each window contributes at most once; single-flight is trade_duration only.
    flight = max(1, trade_duration_ms)
    span = max(e for _, e, _ in scaled) - min(s for s, _, _ in scaled)
    max_iters = max(1, span // flight + len(scaled) + 2)
    remaining = list(scaled)
    for _ in range(max_iters):
        earliest: int | None = None
        for start, end, _trade in remaining:
            entry = max(start, next_free_ms)
            if entry <= end and (earliest is None or entry < earliest):
                earliest = entry
        if earliest is None:
            break
        best_i = -1
        best_trade = Decimal(0)
        for i, (start, end, trade) in enumerate(remaining):
            entry = max(start, next_free_ms)
            if entry == earliest and entry <= end and trade > best_trade:
                best_trade = trade
                best_i = i
        if best_i < 0 or best_trade <= 0:
            break
        profit += best_trade
        next_free_ms = earliest + flight
        # Consume the window (one entry per window at portfolio level).
        remaining.pop(best_i)
    return profit


def _percentile_ms(durations: list[int], p: float) -> int:
    if not durations:
        return 0
    ordered = sorted(durations)
    if len(ordered) == 1:
        return ordered[0]
    # Nearest-rank.
    k = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[k]


def threshold_sweep(
    samples: Sequence[EdgeSample],
    *,
    thresholds_bps: Sequence[Decimal],
    max_gap_ms: int,
    reentry_cooldown_ms: int,
    trade_duration_ms: int = 5_000,
    span_ms: int,
    max_trade_usd: Decimal | None = None,
) -> list[SweepRow]:
    """Sweep ``min_edge_bps`` and report window stats + capturable profit/day.

    Windows are detected once at the minimum threshold (usually 0), then each
    higher T keeps only windows whose ``peak_edge_bps >= T``. That keeps
    capturable profit **monotone non-increasing** in T (same fire-on-open
    trades, progressively dropped weak windows) so the 70% knee fit is well
    defined. Re-detecting at each T can *raise* profit via fragmentation and
    pins the fit to the grid ceiling.
    """
    if span_ms <= 0:
        raise ValueError("span_ms must be positive")
    days = Decimal(span_ms) / Decimal(86_400_000)
    ordered_thr = sorted(thresholds_bps)
    base_thr = ordered_thr[0] if ordered_thr else Decimal(0)
    base_wins = detect_windows(
        samples, min_edge_bps=base_thr, max_gap_ms=max_gap_ms
    )
    rows: list[SweepRow] = []
    for thr in ordered_thr:
        wins = [w for w in base_wins if w.peak_edge_bps >= thr]
        durs = [w.duration_ms for w in wins]
        profit = capturable_profit_single_flight(
            wins,
            reentry_cooldown_ms=reentry_cooldown_ms,
            trade_duration_ms=trade_duration_ms,
            max_trade_usd=max_trade_usd,
        )
        n = len(wins)
        per_day = float(n) / float(days) if days > 0 else 0.0
        profit_day = profit / days if days > 0 else Decimal(0)
        rows.append(
            SweepRow(
                min_edge_bps=thr,
                n_windows=n,
                windows_per_day=per_day,
                duration_median_ms=_percentile_ms(durs, 0.5),
                duration_p90_ms=_percentile_ms(durs, 0.9),
                capturable_profit_usd=profit,
                capturable_profit_per_day=profit_day,
            )
        )
    return rows


def fit_min_edge_bps(
    sweep: Sequence[SweepRow],
    *,
    capture_fraction: Decimal = Decimal("0.70"),
) -> ThresholdFit:
    """Knee-fit: highest threshold retaining ≥ ``capture_fraction`` of P(T_min).

    Sweep rows sort by ``min_edge_bps`` ascending; the first row is the baseline
    (conventionally 0 bps). Walking upward, keep the last row whose capturable
    profit is still ≥ ``capture_fraction * P_baseline``. That is the most
    selective threshold that still captures the required profit mass — what the
    bot config consumes as ``min_edge_bps``.
    """
    if not sweep:
        return ThresholdFit(
            min_edge_bps=Decimal(0),
            capture_fraction=capture_fraction,
            profit_at_fit=Decimal(0),
            profit_at_zero=Decimal(0),
            windows_per_day=0.0,
            profit_per_day=Decimal(0),
            fitted=False,
        )
    ordered = sorted(sweep, key=lambda r: r.min_edge_bps)
    baseline = ordered[0]
    p0 = baseline.capturable_profit_usd
    if p0 <= 0:
        return ThresholdFit(
            min_edge_bps=baseline.min_edge_bps,
            capture_fraction=capture_fraction,
            profit_at_fit=p0,
            profit_at_zero=p0,
            windows_per_day=baseline.windows_per_day,
            profit_per_day=baseline.capturable_profit_per_day,
            fitted=False,
        )
    target = p0 * capture_fraction
    # Scan the full sweep (no early break): keep the *highest* T that still
    # clears the capture bar. With monotone-filtered windows this is the knee;
    # without the break, a mid-sweep dip cannot hide a later recovery either.
    chosen = baseline
    for row in ordered:
        if row.capturable_profit_usd >= target:
            chosen = row
    return ThresholdFit(
        min_edge_bps=chosen.min_edge_bps,
        capture_fraction=capture_fraction,
        profit_at_fit=chosen.capturable_profit_usd,
        profit_at_zero=p0,
        windows_per_day=chosen.windows_per_day,
        profit_per_day=chosen.capturable_profit_per_day,
        fitted=True,
    )


def _assert_homogeneous(samples: Sequence[EdgeSample]) -> None:
    s0 = samples[0]
    for s in samples[1:]:
        if (
            s.pair_id != s0.pair_id
            or s.direction != s0.direction
            or s.session != s0.session
            or s.venue != s0.venue
        ):
            raise ValueError(
                "detect_windows expects a homogeneous "
                "(pair, direction, session, venue) series"
            )
        # AMM rungs share one size; RFQ poll-native rows may vary in size_usd
        # (pnl already scaled). Only enforce size equality for AMM.
        if s.venue == "amm" and s.size_usd != s0.size_usd:
            raise ValueError(
                "detect_windows AMM series must share one size_usd"
            )
    prev = samples[0].ts_ms
    for s in samples[1:]:
        if s.ts_ms < prev:
            raise ValueError("samples must be sorted by ts_ms ascending")
        prev = s.ts_ms


def usdc_premium_bps_from_mid(mid: Decimal) -> Decimal:
    """Convert a USDCUSDT mid to signed USDC-premium bps (WHI-909).

    Sign convention (same as ``edge.basis_wear_bps`` / WHI-960):
    * ``mid > 1`` → positive bps → USDC is richer than USDT.
    * Paying USDC (``buy_fluxion_sell_bybit``) is **charged** this premium.
    * Receiving USDC (``buy_bybit_sell_fluxion``) is **credited**.

    Example: mid ``1.00075`` → ``7.5`` bps. Mid must be positive.
    """
    if mid <= 0:
        raise ValueError(f"USDCUSDT mid must be > 0, got {mid}")
    return (mid - Decimal(1)) * BPS


def extended_threshold_grid_bps(
    *,
    fine_max: int = 60,
    fine_step: int = 2,
    coarse_max: int = 200,
    coarse_step: int = 5,
) -> list[Decimal]:
    """Build the WHI-909 threshold sweep: 0..fine_max step fine, then coarse.

    Default: 0..60 step 2, then 65..200 step 5. Inclusive endpoints. Deduped
    and sorted so a caller can pass the list straight into ``threshold_sweep``.
    """
    if fine_step <= 0 or coarse_step <= 0:
        raise ValueError("threshold steps must be positive")
    if fine_max < 0 or coarse_max < fine_max:
        raise ValueError("require 0 <= fine_max <= coarse_max")
    fine = list(range(0, fine_max + 1, fine_step))
    # Start coarse just past fine_max so we do not duplicate the joint.
    coarse_start = fine_max + coarse_step
    # Align coarse_start to the coarse grid when fine_max is not on it.
    rem = coarse_start % coarse_step
    if rem:
        coarse_start += coarse_step - rem
    coarse = list(range(coarse_start, coarse_max + 1, coarse_step))
    # Always include coarse_max when it is above fine_max.
    if coarse_max > fine_max and (not coarse or coarse[-1] != coarse_max):
        if coarse_max % coarse_step == 0 or coarse_max not in fine:
            if coarse_max not in coarse:
                coarse.append(coarse_max)
    out = sorted({Decimal(v) for v in fine + coarse})
    return out


def apply_rebalance_amortization(
    sample: EdgeSample,
    *,
    rebalance_amortized_bps: Decimal,
    skew_direction: str = SKEW_BUILDING_DIRECTION,
) -> EdgeSample:
    """Subtract amortized rebalance cost from a scored sample (WHI-909).

    Model: cost per cycle = fixed (withdraw fee + gas) + variable (spot
    conversion bps), divided by batch notional → ``rebalance_amortized_bps``.
    Charged **only** on the skew-building direction (default
    ``buy_fluxion_sell_bybit``); the reverse direction is a skew-reducing
    unwind and pays nothing extra here.

    Does not touch venue math — pure post-process on already-scored samples.
    """
    if rebalance_amortized_bps < 0:
        raise ValueError("rebalance_amortized_bps must be >= 0")
    if rebalance_amortized_bps == 0 or sample.direction != skew_direction:
        return sample
    if sample.size_usd <= 0:
        return sample
    cost_usd = rebalance_amortized_bps / BPS * sample.size_usd
    new_pnl = sample.pnl_usd - cost_usd
    new_bps = new_pnl / sample.size_usd * BPS
    return replace(sample, edge_bps=new_bps, pnl_usd=new_pnl)


def apply_rebalance_amortization_many(
    samples: Sequence[EdgeSample],
    *,
    rebalance_amortized_bps: Decimal,
    skew_direction: str = SKEW_BUILDING_DIRECTION,
) -> list[EdgeSample]:
    """Map ``apply_rebalance_amortization`` over a series (preserves length).

    Does **not** drop non-positive PnL samples — those remain as window
    separators for ``detect_windows`` (which already requires ``pnl_usd > 0``
    to open a window). Filtering here would glue adjacent windows and change
    segmentation relative to the pre-rebalance series.
    """
    return [
        apply_rebalance_amortization(
            s,
            rebalance_amortized_bps=rebalance_amortized_bps,
            skew_direction=skew_direction,
        )
        for s in samples
    ]


def as_of_value(
    ts_list: Sequence[int],
    values: Sequence[Decimal],
    ts_ms: int,
    *,
    max_age_ms: int | None = None,
) -> Decimal | None:
    """Rightmost value with ``ts_list[i] <= ts_ms`` (optional max age).

    ``ts_list`` must be sorted ascending and parallel to ``values``.
    """
    import bisect

    if not ts_list or len(ts_list) != len(values):
        return None
    i = bisect.bisect_right(ts_list, ts_ms) - 1
    if i < 0:
        return None
    if max_age_ms is not None and ts_ms - ts_list[i] > max_age_ms:
        return None
    return values[i]


__all__ = [
    "BPS",
    "SKEW_BUILDING_DIRECTION",
    "EdgeSample",
    "OpportunityWindow",
    "SweepRow",
    "ThresholdFit",
    "apply_rebalance_amortization",
    "apply_rebalance_amortization_many",
    "as_of_value",
    "capturable_profit_single_flight",
    "detect_windows",
    "extended_threshold_grid_bps",
    "fit_min_edge_bps",
    "portfolio_capturable_profit",
    "threshold_sweep",
    "usdc_premium_bps_from_mid",
]
