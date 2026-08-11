# bybit-mantle-arbitrage-monitor

Bybit spot ⇄ Fluxion (Mantle) **tokenized stocks (xStocks)** live arbitrage panel.

**This tool places no orders and executes no trades.** Phase 1 needs no exchange API
keys. Phase 2 will use public market data only.

## Status

| Phase | What | Where |
|-------|------|--------|
| **1 (archived)** | 29-day offline WMNT/USDT0 feasibility backtest | `src/mba/`, `report/`, tag `phase1-backtest` |
| **2 (active)** | Real-time Bybit ⇄ Fluxion xStocks panel (TUI + Web) | `src/monitor/` + `web/`, Linear WHI-732…757+ |

Spec of record: [`docs/DESIGN.md`](docs/DESIGN.md). Agent workflow: [`AGENTS.md`](AGENTS.md).

---

## Phase 2 (active)

Live paper-arb monitor over a fixed list of Fluxion-liquid xStocks:

- Prices: AMM pool quote **and** RFQ quote as separate columns (Fluxion is V2/V3 AMM + xChange Atomic RFQ)
- Data: pure realtime, no historical backfill
- Economics: two-sided inventory paper arb (Bybit xStocks taker **0.15%** / 15 bps + Fluxion pool fee + Mantle gas + bilateral slippage; WHI-1042)
- Stats: split by US equity open vs closed session
- UI: TUI (Python, frozen for new features) + Web (Next.js static export +
  FastAPI on the VPS; WHI-757 skeleton)

Milestones (Linear project *Mantle <> Bybit Arbitrage Monitor*):

`M0 WHI-736` → `M1 WHI-730` → `M2 WHI-731` → `M3 WHI-732` / `M4 WHI-733` → `M5 WHI-734` → Web `WHI-757`…

M0–M5 landed. Web skeleton: WHI-757. Next: overview polish WHI-758, PnL v2 WHI-756.

### Setup (phase 2)

```bash
uv sync
cp .env.example .env   # optional MANTLE_RPC_URL; LINEAR_API_KEY if no Linear MCP
git config core.hooksPath .githooks   # once per clone/worktree
```

Phase-2 modules live under `src/monitor/`. Reuse from phase 1 is listed in
`docs/DESIGN.md` §4.2 (rpc / V3 quote math / Bybit fee·slippage / attribution heuristics).

### Live collector (M2)

Long-running daemon: Bybit public WS (L1 book + trades, de-multiplied) + Mantle
per-block Multicall3 pool state + Swap/LOP logs + RFQ quote poll → SQLite
(`data/monitor.db` by default; gitignored).

```bash
uv run python -m monitor.collector
# optional overrides:
uv run python -m monitor.collector --sqlite /tmp/monitor.db
```

Config: `config/pairs.yaml` (inventory) + `config/collector.yaml` (WS/RPC/RFQ tunables).
No trading credentials. Optional keyed `MANTLE_RPC_URL` in `.env` for less throttling.

---

## Phase 1 (archived) — WMNT/USDT0 feasibility backtest

A POC that answered one question with numbers rather than intuition:

> Over the past ~29 days, did a profitable arbitrage window exist between
> WMNT/USDT0 on Mantle's two main DEXes and Bybit spot MNTUSDT — how often, and
> **for how long**?

Frozen at tag **`phase1-backtest`**. Code remains runnable under `src/mba/` so the
delivered reports can be regenerated.

### Running the phase-1 pipeline

Each stage reads the previous one's Parquet under `data/` (gitignored; regenerate or
restore locally). Use `python -u` so progress lines flush.

```bash
uv run python -u -m mba.m1_scan_events    # scan Swap/Mint/Burn logs
uv run python -u -m mba.m2_quotes         # quote both pools at every such block
uv run python -u -m mba.m3_bybit          # Bybit tape + live book
uv run python -u -m mba.m6_attribution    # who traded, what they made
uv run python -u -m mba.m4_align          # align venues, net profit
uv run python -u -m mba.m5_report         # windows, report, charts
```

**M6 before M5, despite the numbering.** M6 only needs M1 and M3; its decoded swap
directions let M5's did-anyone-take-it check match on direction.

### Phase-1 outputs

```
report/report.md         window counts, durations, go/no-go vs carry
report/attribution.md    who actually traded these pools
report/*.png             cost vs size, duration CDF, % time profitable, P&L series
data/*.parquet           intermediates (not in git)
```

### Why the method is what it is

**Pool state is piecewise-constant.** It only moves on Swap/Mint/Burn (Agni) and
Swap/DepositedToBins/WithdrawnFromBins (Moe). Quoting at exactly those blocks
yields an **exact step function**, not a sample.

**Profitability factors into a step-function comparison.** Over one DEX interval
the quantity and USD leg are fixed, so net profit is affine in the Bybit mid with
constant slope. That turns duration measurement into an exact comparison.

**Agni is a PancakeSwap-V3 fork, not vanilla Uniswap V3.** Its `Swap` event
carries two extra `protocolFees` arguments, so the Uniswap `topic0` matches
nothing.

### Phase-1 known limits

- Bybit depth beyond L1 comes from a single live orderbook snapshot applied as a
  multiplicative slippage-vs-mid curve; historical L2 is not published.
- Window durations resolve to Bybit print times (coarser than the 4s actionable floor).
- Mantle archive state reaches only ~30 days.
- RPC is load-balanced and not read-your-writes consistent (`HEAD_LAG_BLOCKS`).

### Out of scope (both phases, by design)

No order placement or trade execution. No triangular or multi-hop routing. No
MEV / gas-auction modeling as a product feature. No venues beyond those named in
the active phase.

---

## Agent / process layer

Bootstrapped from [`Whisker17/code-template`](https://github.com/Whisker17/code-template)
(`154e86bb`). Workflow docs:

| Path | Role |
|------|------|
| `AGENTS.md` | agent operating rules (canonical; `CLAUDE.md` is a symlink) |
| `docs/DESIGN.md` | PRD / spec of record |
| `docs/GIT_WORKFLOW.md` | main ≡ production, worktree-per-issue, merge lanes |
| `.claude/skills/` | vendored engineering skills |
| `.githooks/pre-push` | blocks direct push to `main` / `dev` |
