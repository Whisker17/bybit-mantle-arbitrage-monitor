# AGENTS.md

This file provides guidance to coding agents (Claude Code, Codex, etc.) working in this
repository. `CLAUDE.md` is a symlink to this file — edit here only.

## What this is

Bybit spot ⇄ Fluxion (Mantle) tokenized-stocks (xStocks) live arbitrage panel. Phase-1 WMNT/USDT0 offline backtest is archived under src/mba and report/

The full PRD — requirements, architecture, milestones, rejected alternatives, open
risks — lives in `docs/DESIGN.md`. Read it before making any design or architectural
decision; do not re-derive parameters or decisions that are already validated there.

## Status

<!-- Keep this section current: what has landed, what is architected-for but NOT
implemented yet. Update it the moment reality changes instead of leaving stale
placeholders. Agents must not assume a module exists until its issue lands. -->

- **Phase 1 (archived, tag `phase1-backtest`):** offline WMNT/USDT0 backtest under
  `src/mba/` with delivered reports in `report/`. Pipeline still runs against local
  parquet under `data/`.
- **Phase 2 (active):** Bybit ⇄ Fluxion xStocks live panel. Product decisions are in
  `docs/DESIGN.md`.
  - **M1 (WHI-730) landed:** `config/pairs.yaml` + `monitor/symbols` (fixed overlap
    list, Bybit multiplier map, RFQ mode `pollable_quote`). Research notes under
    `docs/references/m1-*.md`.
  - **M2 (WHI-731) landed:** live collectors — `monitor/bybit` (WS book+trades),
    `monitor/fluxion` (per-block pool state, swaps, RFQ poll + LOP fills),
    `monitor/storage` (SQLite), `monitor/collector` daemon
    (`python -m monitor.collector`). Tunables in `config/collector.yaml`.
    Block ingest SLO / `head_lag_blocks: 1` measured WHI-749
    (`docs/references/m2-block-ingest-latency.md`, DESIGN §5.2).
  - **M3 (WHI-732) landed:** `monitor/metrics` — spread bps (Bybit mid vs AMM /
    RFQ), net paper edge with wear breakdown at $1K/$5K/$20K, NYSE open/closed
    session segmentation, cumulative P50/P95/P99/max + cost-floor breach stats.
    Tunables in `config/metrics.yaml`.
  - **M4 (WHI-733) landed:** `monitor/attribution` — mechanism RFQ/AMM labels,
    AMM taker behavior pipeline (contract/EOA, convergence, activity regime,
    Bybit lead-lag → arb_bot / price_keeper / retail / unknown), pair aggregates
    for M5. Tunables in `config/attribution.yaml`; rules in
    `docs/references/m4-attribution-labels.md`.
  - **M5 (WHI-734) landed:** `monitor/tui` — Textual live panel (overview table
    + pair detail). Reads collector SQLite; spreads/edge via M3, attribution via
    M4. Tunables in `config/tui.yaml`. Entry: `python -m monitor.tui`.
  - **Retention (WHI-751) landed:** SQLite prune + `bybit_book` → 1m downsample,
    disk waterline (warn/critical), in-collector loop + `python -m monitor.retention`.
    Policy in `config/collector.yaml` `retention:`; design math in DESIGN §5.1.
  - **Closed-session RFQ research (WHI-753) landed:** weekend RFQ still
    two-sided on liquid pairs and tracks Bybit mid — session ≠ mechanism; note
    in `docs/references/m4-closed-session-rfq.md` + DESIGN / M4 rule updates.
  - **PnL v2 research (WHI-754) landed:** Hummingbot CEX⇄AMM methodology →
    cash-flow PnL spec; note `docs/references/hummingbot-pnl.md`, DESIGN §2.6.
    Engine (optimal size + bucket table) is **WHI-756** — not landed.
  - **PnL v2 depth collector (WHI-755) landed:** Bybit `orderbook.50` →
    stateful N-level book; L1 still `bybit_book`; precomputed bucket VWAP curve
    in `bybit_depth` (throttled). Config `bybit.depth` in `config/collector.yaml`.
  - **Web skeleton (WHI-757) landed:** `monitor/api` (FastAPI read-only over
    SQLite; reuses TUI builders), `web/` (Next.js static export), `deploy/` +
    `scripts/deploy-web.sh` (systemd + nginx). Tunables in `config/api.yaml`.
    TUI frozen for new features — Web is the surface for new metrics.
  - **Web overview (WHI-758) landed:** dark Tailwind overview table (TUI-parity
    columns + status bar + stale yellow banner + sort/filter + 2s poll + row
    → `/pair/{id}/`). Bucket PnL column placeholder until WHI-756. Pair detail
    route is a stub (full detail = WHI-759).
  - **Not landed yet:** pair detail Web (WHI-759), PnL v2 engine (WHI-756).
    Do not assume those modules exist until their issues land.
## Build, test, run

```bash
uv sync                                       # install deps (creates .venv)
uv run pytest                                 # unit tests
uv run ruff check .                           # lint
uv run mypy                                   # type check
# Phase-1 pipeline (needs data/ parquet from a prior run):
uv run python -u -m mba.m5_report             # regenerate report/ from local parquet
# Phase-2 live collector (M2 / WHI-731); needs network + optional MANTLE_RPC_URL:
uv run python -m monitor.collector
# Phase-2 TUI (M5 / WHI-734); reads collector SQLite (default data/monitor.db):
uv run python -m monitor.tui
# Optional: uv run python -m monitor.tui --db /path/to/monitor.db
# Journal retention (WHI-751); one-shot prune / growth report:
uv run python -m monitor.retention --growth-only
uv run python -m monitor.retention
# Block ingest latency probe (WHI-749); chain-only, no Bybit/RFQ:
uv run python -m monitor.collector.latency_probe --duration-s 600
# Phase-2 read-only Web API (WHI-757); needs collector journal:
uv run python -m monitor.api
# Optional: uv run python -m monitor.api --host 127.0.0.1 --port 8000
# Web static export (build on laptop/CI — never on the 1GB VPS):
#   cd web && npm ci && npm run build   # → web/out
# Web pure-helper unit tests (format/sort):
#   cd web && npm test
# Deploy to VPS (rsync out/ + API sources, restart systemd):
#   ./scripts/deploy-web.sh user@host
```

## Runtime configuration

Secrets live in `.env` at the repo root (`.env.example` is the checked-in
template), loaded at startup — a missing required var must fail fast with a clear
error. **Never commit `.env`.** Non-secret runtime parameters (thresholds, feature
flags, tunables) live in `config/` as validated, typed config — not hardcoded, not in
`.env`. See `config/README.md` for the convention.

## Architecture

Module layout is fixed by `docs/DESIGN.md` §4.2. Short mirror:

- **`mba/`** — phase-1 offline WMNT/USDT0 backtest (archived, still runnable). Do not
  extend for xStocks.
- **`monitor/`** — phase-2 live Bybit ⇄ Fluxion xStocks panel. All new product code.
  - **`monitor/symbols`** (M1) — fixed pair list + Bybit multiplier helpers.
  - **`monitor/bybit`**, **`monitor/fluxion`**, **`monitor/storage`**,
    **`monitor/collector`** (M2) — live feeds → SQLite.
  - **`monitor/metrics`** (M3) — edge, wear, session stats from quote ticks.
  - **`monitor/attribution`** (M4) — mechanism + behavior labels / aggregates.
  - **`monitor/tui`** (M5) — Textual overview + detail panel over SQLite
    (**frozen** for new features).
  - **`monitor/api`** (WHI-757) — FastAPI read-only JSON over the journal.
  - **`web/`** (WHI-757+) — Next.js static export; overview (WHI-758);
    deploy via `scripts/deploy-web.sh` + `deploy/`.
  - Still to land: pair detail Web (WHI-759), PnL v2 (WHI-756).
  Reuse pieces from `mba` per DESIGN §4.2 table; do not import whole stages.

## Git workflow (mandatory)

**One issue = one git worktree off latest `origin/dev` = one PR into `dev`.**
Do **not** implement issues in the primary clone working tree.

1. `git fetch` + create worktree/branch from `origin/dev`
   (`fix/whi-NNN-topic` or `feat/whi-NNN-topic`).
   **Check the issue's labels first** — an issue labelled `hotfix` branches off
   `origin/main` instead and targets `main` (see **Promotion lanes** below). Verify the
   base right after creating the worktree — `git merge-base HEAD origin/dev` must equal
   `git rev-parse origin/dev` — whatever tooling created it. *(Runtime aside: Claude
   Code's `EnterWorktree` defaults to `origin/main`, wrong for this lane. Any wrapper may
   have its own default; the check above is what settles it.)*
2. Implement only that issue; tracker state → **`In Progress`**.
3. `gh pr create --base dev` (title/body include `WHI-NNN`); tracker →
   **`In Review`**. Any review finding you intentionally leave unfixed goes in
   `docs/DEFERRED_ISSUES.md` as part of this PR — see that file for the format.
4. A PR whose implementation went through `/implement`'s full three-round review loop
   (plus the escalation pass, when round 3 left findings open) is **pre-authorized to
   self-squash-merge** once it reads MERGEABLE/CLEAN and tests + lint pass — no separate
   human approval. **Exceptions that stop at `In Review` for a human:** changes touching
   **key handling, RPC credentials**, and `release/*` → `main` promotions. PRs that skipped the
   review loop also stop at `In Review`. After merging, run the **post-merge cleanup**
   below.

### Post-merge cleanup (mandatory, in order)

Drive these from the **primary clone**; never commit to `dev` directly.

0. **If the PR is CONFLICTING** (`dev` advanced since you branched): inside the feature
   worktree, `git merge origin/dev`, resolve, rerun the affected tests, and `git push`.
   The PR must read **MERGEABLE / CLEAN** before you merge.
1. **Squash-merge + drop the remote branch:** `gh pr merge <N> --squash --delete-branch`.
2. **Remove the worktree:** `git worktree remove <worktree-path>` then
   `git worktree prune`.
3. **Delete the local branch:** `git branch -D fix/whi-NNN-topic`
   (this fails while the worktree still holds the branch — do step 2 first).
4. **Fast-forward local `dev`:** `git fetch origin --prune` then
   `git merge --ff-only origin/dev` (must fast-forward — do not create commits on
   `dev`).
5. **Tracker → `Done`.**

### Promotion lanes (`→ main`)

`main` **equals production** — always the last deployed tag. Never open a PR with `dev` as
head into `main` (the branch would be auto-deleted by `delete_branch_on_merge`). Two lanes
reach `main`, and picking the wrong one ships unreviewed work:

- **Release** — everything on `dev` is shippable. Cut a temporary `release/vX.Y.Z` from
  `dev`, PR → `main`. **Always a human gate.**
- **Hotfix** — production is broken *and* `dev` holds work that must not ship. Branch off
  `origin/main`, PR → `main`, then **merge `main` back into `dev`** or the next release
  re-ships the bug.

The decision rule: run `git log --oneline origin/main..origin/dev`. **If that list holds a
single commit you would not ship right now, you must use the hotfix lane.**

Merge strategy is per-lane: **squash** into `dev`, but **merge commit** into `main` —
squashing a release/hotfix disconnects the tag from `dev`'s history and silently breaks
`git log <tag>..origin/dev`. Bump the project version before tagging, **deploy from the
tag and never from a branch**, and keep the tracker Release ↔ git tag ↔ GitHub Release
triple in agreement (backfill the Release's `commitSha`).

Enable the local push guard once per clone **and per worktree**:
`git config core.hooksPath .githooks`.

Full rules: `docs/GIT_WORKFLOW.md`.

## Template feedback loop

This repo was bootstrapped from the shared project template
(`https://github.com/Whisker17/code-template`). When work here surfaces an improvement that belongs to the
**template layer** — a workflow rule that bit us, a skills configuration fix, a doc
convention worth standardizing — tell the user explicitly so they can port it back to
the template repo (and its `CHANGELOG.md`). Project-specific learnings stay here;
process-level learnings flow back.

## Agent runtime (any agent, any vendor)

This repo is runtime-neutral: Claude Code, Codex, or anything else. Nothing in the
workflow names a model. Instead, skills name a **role** — `REVIEWER`, `ESCALATOR`,
`EXPLORER` — mapped to real commands in `config/agent-roles.conf` and dispatched through
`scripts/agent-dispatch.sh`. Full contract: **`docs/agents/runtime.md`**.

Two rules matter more than the mechanism:

- **Review happens in a different context than implementation**, with a model at least as
  capable (cross-vendor preferred). Check the path before relying on it:
  `scripts/agent-dispatch.sh --probe`.
- **If the reviewer is unavailable, the review loop did not run** — finish the work, open
  the PR, and stop at `In Review` for a human. Self-review in the implementing context
  never authorizes a self-merge.

When a model generation turns over, edit `config/agent-roles.conf` and nothing else.

## Agent skills

Skills live in `.claude/skills/<name>/SKILL.md`. Runtimes that auto-discover them expose
each as `/<name>`; **in a runtime with no skill loader, read the file directly** — a skill
is just markdown. The load-bearing ones:

| Skill | Path |
|-------|------|
| `/implement` | `.claude/skills/implement/SKILL.md` |
| `/code-review` | `.claude/skills/code-review/SKILL.md` |
| `/grill-me` → `/to-spec` → `/to-tickets` | `.claude/skills/{grill-me,to-spec,to-tickets}/SKILL.md` |
| `/tdd`, `/diagnosing-bugs`, `/handoff`, `/triage` | `.claude/skills/<name>/SKILL.md` |
| `/ask-matt` (which skill do I want?) | `.claude/skills/ask-matt/SKILL.md` |

### Issue tracker

Issues and PRDs live in **Linear** (project `Mantle <> Bybit Arbitrage Monitor`, team
`Whisker-Personal`). Access is a fallback ladder — MCP tools, else the GraphQL API with
`LINEAR_API_KEY` — and reaching the tracker is mandatory, not optional: workflow state
moves in lockstep with the PR. External PRs are not a triage surface. See
`docs/agents/issue-tracker.md`.

### Triage labels

Canonical role names (`needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`) used verbatim as Linear labels. See
`docs/agents/triage-labels.md`.

### Domain docs

This repo's spec of record is `docs/DESIGN.md` (PRD: requirements, architecture,
milestones, rejected alternatives, open risks) plus `docs/adr/` for narrower decisions
made after v1 ships. See `docs/agents/domain.md`.
