# Issue Template

The canonical shape for every Linear issue in this workspace. Copy the skeleton in
[§ Copy-paste skeleton](#copy-paste-skeleton), fill each section, and delete the inline
guidance (the `> _italic_` hints). **All issue content is written in English.**

It pairs with:

- `docs/agents/issue-tracker.md` — how to read/write Linear via the MCP tools.
- `docs/agents/triage-labels.md` — the five canonical triage labels.
- `docs/DESIGN.md` — the PRD these issues implement; every issue should trace back to a
  section there.

---

## Principles

A good issue is a **self-contained unit of work**: a competent engineer (or an AFK agent)
can open it cold and know *what* to build, *why*, *where* in the codebase, what it
*depends on*, and *how to know it's done* — without asking a follow-up question.

1. **One issue = one deliverable.** If it needs "and" in the objective, split it.
2. **Concrete over abstract.** Name the files, functions, config keys, and
   `docs/DESIGN.md` section. Reference `docs/DESIGN.md §2.2`, not "the entry logic".
3. **Verifiable.** Every issue ends in acceptance criteria that are objectively
   checkable (a test passes, a log shows the expected decision, a value appears in the
   data store).
4. **Dependencies explicit.** State what blocks this and what this unblocks, so the
   work can be ordered and parallelized.
5. **Scoped.** Say what is *out* of scope as clearly as what is in.
6. **No new parameters without a source.** Anything that reads as a tunable parameter
   must cite where in `docs/DESIGN.md` §2 / `references/` it comes from, or be
   explicitly flagged as a new, unvalidated parameter.

---

## Title convention

```
[Mn] [Component] <imperative, specific description>
```

- **`[Mn]`** — the milestone this issue belongs to (per `docs/DESIGN.md` §6). Omit only
  for standalone issues with no milestone.
- **`[Component]`** — the module this touches, per `docs/DESIGN.md` §4.2, plus
  cross-cutting tags `[Config]`, `[Infra]`, `[Docs]`, `[CI]`.
- **Description** — short, specific, and imperative. Prefer
  `Add request dedup to the scheduler` over `Scheduler stuff`.

---

## Metadata (set on the Linear issue, not in the body)

| Field         | How to set it                                                            |
| ------------- | ------------------------------------------------------------------------ |
| **Project**   | `Mantle <> Bybit Arbitrage Monitor` (always — the only project in scope for this repo). |
| **Milestone** | Attach to the project milestone matching the `[Mn]` title tag.          |
| **Priority**  | `Urgent` / `High` / `Medium` / `Low` — see the table below.             |
| **Labels**    | Triage role from `triage-labels.md` + any type label (`bug`, `feature`, `research`, `chore`, `hotfix`). `hotfix` changes the git base branch — see `triage-labels.md`. |
| **Assignee**  | Set when claimed; leave empty in the backlog.                           |
| **Relations** | Use Linear's native `blocks` / `blocked-by` relations.                  |

### Priority guide

| Priority   | Use when…                                                                      |
| ---------- | ------------------------------------------------------------------------------ |
| **Urgent** | Blocks a milestone, or is a safety item on a key handling, RPC credentials path. Do first. |
| **High**   | Core deliverable of the milestone; needed for it to be "done."                 |
| **Medium** | Valuable but not blocking; can slip a milestone without derailing it.          |
| **Low**    | Nice-to-have, polish, or opportunistic cleanup.                                |

---

## Body sections

Fill the sections below in order. Sections marked _(optional)_ may be dropped when they
add nothing; keep the rest even if brief.

### `## Objective`
One or two sentences: what this issue delivers and why it matters.

### `## Context` _(optional)_
Background a newcomer needs: the relevant `docs/DESIGN.md` section, prior research link,
or an external platform fact. Skip if the Objective is fully self-explanatory.

### `## Blocked By` / `## Blocks`
Dependency graph. List issue identifiers (e.g. `WHI-42`) and a short
reason. Prefer to *also* wire these as Linear `blocked-by` / `blocks` relations; the
body lines are the human-readable mirror. Use `None (entry point)` when there are no
blockers.

### `## Implementation`
The plan of record. Numbered steps, each anchored to a concrete file/module. Include:
- Exact file paths to create or modify.
- Function signatures, config keys, schema, or API payload shapes where they pin the
  design.
- Code blocks for anything the implementer must follow verbatim.
This is the section that makes an issue AFK-ready — be generous with specifics.

### `## Out of scope` _(optional)_
Explicitly list what this issue does **not** cover, to prevent scope creep and to signal
where follow-up issues pick up.

### `## Acceptance criteria`
A checklist of objectively verifiable conditions. Each item is a fact someone can
confirm: a passing test, a log line, a stored record, a delivered notification. If you
can't check it, it's not a criterion — rewrite it.

### `## Testing / Verification` _(optional)_
How to prove the criteria hold: exact commands, fixtures to replay, or manual steps and
expected output. Merge into Acceptance criteria for small issues.

### `## References` _(optional)_
Links to `docs/DESIGN.md` sections, `docs/references/`, prior issues, or external docs.
Bare URLs are fine.

---

## Copy-paste skeleton

```markdown
## Objective
> _One or two sentences: what this delivers and why. State the success outcome._

## Context
> _(optional) Background, docs/DESIGN.md section, platform facts._

## Blocked By
> _Issue ids + one-line reason, or `None (entry point)`._

## Blocks
> _Issue ids this unblocks, or `None`._

## Implementation
> _Numbered, file-anchored plan. Signatures, config keys, schema, code blocks._
1.
2.
3.

## Out of scope
> _(optional) What this issue deliberately does not cover._

## Acceptance criteria
- [ ]
- [ ]
- [ ]

## Testing / Verification
> _(optional) Exact commands / fixtures / manual steps + expected output._

## References
> _(optional) Links to docs/DESIGN.md sections, references/, prior issues._
```

---

## Appendix: Milestones

Issues carry an `[Mn]` tag because they live under a **milestone**, per
`docs/DESIGN.md` §6. Keep the milestone list there as the source of truth; if milestones
change, update §6 — not this file.
