# Git Workflow

This repo uses the **main + dev + issue-worktree** model.

## The one-sentence rule

**Every issue's implementation = a dedicated git worktree branched off latest `dev` +
a short-lived branch → PR into `dev` → tracker state moves in lockstep with the PR.**

Never develop an issue directly in the primary clone's working tree (it dirties the
`dev` workspace and blocks parallel work on multiple issues). The primary clone is only
for: pulling `dev`/`main`, creating worktrees, and post-merge verification.

## Branch roles

| Branch | Role | Who writes |
|--------|------|------------|
| `main` | **Equals production.** Always equals the last deployed tag. Only accepts merges from `release/*` and `hotfix/*`. | PR only |
| `dev` | Active integration branch for the next version. **May contain changes not yet validated in production.** The **only** PR target for day-to-day work. | PR from issue branches |
| `feat/*` `fix/*` `chore/*` | Single-issue implementation branches (inside worktrees) | Short-lived |
| `hotfix/*` | Emergency production fixes (branched from `main`) | Sync back to `dev` after merging to `main` |
| `release/*` | Temporary promotion branches (cut from `dev`, PR → `main`) | Short-lived |

`main` ≡ production is what makes the two promotion lanes distinguishable: a release
ships everything on `dev`, a hotfix ships *only* your fix on top of what is already
live. Treat `origin/main` as the definition of "the code currently running".

## Issue lifecycle (mandatory)

Aligned with the tracker workflow states: `Todo` → `In Progress` → `In Review` → `Done`.

```text
                fetch latest dev
                      │
                      ▼
         git worktree add (branch off origin/dev)
                      │
                      ▼
              implement one issue
                      │
                      ▼
         push + gh pr create --base dev
         tracker: state = In Review
                      │
                      ▼
              review + tests/lint green
                      │
                      ▼
         squash-merge PR → dev
         tracker: state = Done
         remove worktree + prune branch
```

### 1. Start: new worktree off latest `dev`

From the primary clone (adjust paths to your machine; a sibling directory is
recommended):

```bash
git fetch origin
git checkout dev
git pull --ff-only origin dev

# Branch name: type/whi-<id>-short-topic (all lowercase, dash-separated)
ISSUE=whi-123
BRANCH=feat/${ISSUE}-short-topic
WT="../bybit-mantle-arbitrage-monitor-wt/${ISSUE}"

git worktree add -b "$BRANCH" "$WT" origin/dev
cd "$WT"

# Verify the base immediately — these two values must be equal
git merge-base HEAD origin/dev
git rev-parse origin/dev
```

Tracker: set the issue to **`In Progress`**. Optionally note the worktree path and
branch name on the issue.

If the tracker suggests a branch name (e.g. Linear's `gitBranchName`), you may use it —
but the base **must** be the latest `origin/dev`, never a stale tip.

> ⚠️ **Base trap.** Never assume your tooling's default base. Run the `merge-base` /
> `rev-parse` check above right after creating **any** worktree and confirm the two values
> match the base you intended.
>
> *Runtime aside:* Claude Code's `EnterWorktree` branches from
> `origin/<default-branch>` — which is `main` in this layout, so it is **wrong for the
> issue lane** (you want `dev`) and coincidentally **right for the hotfix lane**. Other
> wrappers have other defaults, which is precisely why the check is unconditional.

### 2. Implement

- **One worktree / one branch / one issue / one PR** (never bundle unrelated issues)
- Small commits (`feat:` `fix:` `chore:` `docs:` `refactor:` `test:`)
- Run the relevant tests inside the worktree; the full gate runs again before merge
- Never commit secrets, `.env`, large logs, or local data files

### 3. Open PR → `dev`, enter review

```bash
git push -u origin HEAD
gh pr create --base dev \
  --title "feat(WHI-123): short description" \
  --body "$(cat <<'EOF'
## Summary
- ...

## Tracker
Closes WHI-123

## Test plan
- [ ] ...
EOF
)"
```

Tracker: set the issue to **`In Review` immediately** (when the PR opens — not after
merge).

PR conventions:

- **base must be `dev`** (features/fixes never target `main` directly — the one exception
  is an issue labelled `hotfix`, see [§ Hotfix](#hotfix))
- Title carries `WHI-NNN`
- Body links the tracker issue
- Merge strategy: **squash and merge** (per-lane rules:
  [§ Merge strategy](#merge-strategy-per-lane))
- Remote branch is auto-deleted on merge (`delete_branch_on_merge`)

### 4. Review, merge → Done

**Fast path — PRs that went through `/implement`'s full three-round review loop** (two
independent reviewers per round on the Standards + Spec axes, each in a fresh context
outside the implementing one, three fix-and-verify rounds; findings still open after round
3 get an escalation fix pass, and only findings that are genuinely out of the issue's
scope go to `docs/DEFERRED_ISSUES.md`): that loop **is** the review. Once the PR reads
MERGEABLE/CLEAN and the full test + lint gate passes, the implementing agent
squash-merges and runs cleanup itself — no separate human approval.

Which model fills the reviewer seat is per-runtime and defined by
`docs/agents/runtime.md`. The requirement this fast path rests on is not a specific model
but the **separation**: reviewed by a different context, at least as capable as the
implementer. A self-review inside the implementing context does not open the fast path.

**Exceptions that always stop at `In Review` for a human:**

- Changes touching **key handling, RPC credentials** (defined per-project at setup; e.g. payment
  flows, auth, production data migrations, key handling)
- `release/*` → `main` promotions
- PRs that skipped the review loop (human-implemented, or loop not run)

For those, a human reviewer:

1. Reviews the PR (code + whether it stays within the issue's scope)
2. Verifies against latest `dev` before merging (primary clone or a clean worktree):
   run the relevant tests / dry-run
3. Squash-merges the PR into `dev`
4. Tracker: set the issue to **`Done`**
5. Cleans up the local worktree (see below)

### Post-merge cleanup (mandatory, in order)

Drive these from the **primary clone**; never commit to `dev` directly.

0. **If the PR is CONFLICTING** (`dev` advanced since you branched): inside the feature
   worktree, `git merge origin/dev`, resolve, rerun the affected tests, and `git push`.
   The PR must read **MERGEABLE / CLEAN** before you merge.
1. **Squash-merge + drop the remote branch:** `gh pr merge <N> --squash --delete-branch`
2. **Remove the worktree:** `git worktree remove <worktree-path>` then
   `git worktree prune`
3. **Delete the local branch:** `git branch -D feat/whi-123-topic`
   (fails while the worktree still holds the branch — do step 2 first)
4. **Fast-forward local `dev`:** `git fetch origin --prune` then
   `git merge --ff-only origin/dev` (must fast-forward — never create commits on `dev`)
5. **Tracker → `Done`**

### State mapping (tracker ↔ git)

| Stage | Tracker state | Git |
|-------|---------------|-----|
| Not started | `Backlog` / `Todo` | no branch |
| Implementing | `In Progress` | worktree + branch exist, no PR (or draft) |
| PR open, awaiting review | **`In Review`** | open PR → `dev` |
| Merged | **`Done`** | squash-merged into `dev`, worktree removed |
| Abandoned | `Canceled` | PR closed, worktree removed, not merged |

Triage labels (`ready-for-agent` / `ready-for-human` / …) are **orthogonal** to workflow
state: labels say *who* does the work, state says *how far along* it is.

## Parallel issues

- One worktree per issue → naturally parallel, no dirty-workspace collisions
- Issues touching the **same module** must not run in parallel; serialize, or wait for
  the earlier PR to merge and branch the next worktree off the new `dev`
- Always `git fetch` + base on `origin/dev` before opening a new worktree

## Choosing a promotion lane: release or hotfix?

Both lanes end in a PR into `main`, and picking the wrong one is how unreviewed work
reaches production. The decision rule is mechanical:

```bash
git fetch origin
git log --oneline origin/main..origin/dev
```

**If that list contains a single commit you are not willing to ship right now, you must
take the hotfix lane.** Cutting a release would drag every one of those commits into
production along with your fix. Only when you are happy to ship the whole list is a
release correct.

| | Release lane | Hotfix lane |
|---|---|---|
| Ships | everything on `dev` | only your fix, on top of what is already live |
| Base | `dev` | **`origin/main`** |
| Use when | `dev` is fully shippable | production is broken and `dev` holds unshippable work |

## Releasing to `main`

**Never** open a PR with `dev` as the head branch into `main` (with
`delete_branch_on_merge` enabled, merging would delete `dev`). Cut a temporary release
branch from `dev`:

```bash
git fetch origin
git checkout dev && git pull --ff-only origin dev
git checkout -b release/v0.1.0

# Bump the project version to match the tag you are about to create
#   (e.g. pyproject.toml `version`), commit it on the release branch
git push -u origin HEAD
gh pr create --base main --title "release: v0.1.0" --body "..."
```

After the PR reads MERGEABLE/CLEAN and a human has approved it (`release/*` → `main` is
**always** a human gate):

1. **Merge with a merge commit, not squash** (see
   [§ Merge strategy](#merge-strategy-per-lane))
2. Create the annotated tag and the GitHub Release: `git tag -a v0.1.0 -m "v0.1.0"` +
   `git push origin v0.1.0`
3. **Deploy from the tag**, never from a branch
4. Backfill the tracker Release's `commitSha` (see [§ Version axis](#version-axis))

Temporary `release/*` branches may be auto-deleted; `dev` lives forever.

## Hotfix

**When to take this lane:** production is broken *and* `dev` already holds work that must
not ship yet — see [§ Choosing a promotion lane](#choosing-a-promotion-lane-release-or-hotfix).
`origin/main` is by definition the code running in production, so it is the only correct
base.

```bash
git fetch origin
git worktree add -b hotfix/whi-123-short-topic \
    ../bybit-mantle-arbitrage-monitor-wt/hotfix-whi-123 origin/main

# Verify the base immediately — these two values must be equal
git -C ../bybit-mantle-arbitrage-monitor-wt/hotfix-whi-123 merge-base HEAD origin/main
git rev-parse origin/main
```

Then:

1. Fix **only** this one issue
2. **Bump the project version to a patch release** (`0.1.5` → `0.1.5.1`) — otherwise tag
   `v0.1.5.1` points at a tree that calls itself `0.1.5`
3. `gh pr create --base main`, title/body carry `WHI-NNN`; tracker →
   `In Review`
4. **Merge with a merge commit, not squash** (see
   [§ Merge strategy](#merge-strategy-per-lane))
5. Tag `v0.1.5.1` + create the GitHub Release
6. **Deploy from the tag**, not from a branch
7. **Merge `main` back into `dev`:** `git checkout dev && git merge origin/main`. Skip this
   and the next release re-ships the bug you just fixed
8. Tracker: issue → `Done`, the corresponding Release → `Released`, and fill in that
   Release's `commitSha`

When production is live, hotfixes outrank regular issues — anything on a
key handling, RPC credentials path takes this route.

## Version axis

The version number is **pinned in advance** and appears in three places that must agree.
If any one of them disagrees, a step was skipped:

| Place | Artifact | Question it answers |
|-------|----------|---------------------|
| Tracker | a Release / version entity (`0.1.5`, `0.1.5.1`, `0.1.6`, …) | **Plan**: which issues are in this version |
| Git | annotated tag `v0.1.5` | **Fact**: which tree this version is |
| GitHub | the GitHub Release on that tag | **Ship**: public changelog and artifacts |

- Every issue belongs to exactly one tracker Release. Once a Release has issues attached,
  those issues leave `Backlog` for `Todo`.
- After shipping, backfill the tracker Release's **`commitSha`** (the commit the tag points
  at). This is the only authoritative binding between "a version" and "some code".
- Milestones and Releases are **orthogonal axes**: a milestone is *which capability stage*,
  a Release is *when it ships*. Don't use milestones to express release batches.
- Hotfixes use a four-segment version (`0.1.5.1` — valid and correctly ordered under
  PEP 440 and semver-tools alike). **Don't** reuse the next minor number; the tracker has
  already promised that number to a feature batch.

## Merge strategy (per lane)

| PR | Merge with | Why |
|----|-----------|-----|
| `feat/*` `fix/*` `chore/*` → `dev` | **squash** | One issue, one commit; clean `dev` history |
| `release/*` → `main` | **merge commit** | Preserves ancestry |
| `hotfix/*` → `main` | **merge commit** | Same |

**Why releases and hotfixes must not be squashed:** squashing creates a brand-new commit
on `main` that exists nowhere in `dev`'s history, so the tag lands on a branch
disconnected from `dev`. The damage is silent but permanent:
`git merge-base --is-ancestor v0.1.5 origin/dev` returns false, and
`git log v0.1.5..origin/dev` — "what's changed since the last release" — returns garbage.
Both of those are the queries you most want to trust at release time.

This means the GitHub repo must have **both** `Allow squash merge` and
`Allow merge commit` enabled, with the lane deciding which you use.

## Branch naming

| Type | Format | Example |
|------|--------|---------|
| Feature | `feat/whi-<id>-<topic>` | `feat/whi-101-user-auth` |
| Fix | `fix/whi-<id>-<topic>` | `fix/whi-112-race-condition` |
| Chore | `chore/whi-<id>-<topic>` | `chore/whi-108-lint-config` |
| Hotfix | `hotfix/whi-<id>-<topic>` | `hotfix/whi-140-login-loop` |
| Release | `release/v<version>` | `release/v0.1.0`, `release/v0.1.5.1` |

- All lowercase, words joined with `-`
- **Must include the tracker id** (`whi-NNN`) for PR ↔ issue tracing —
  hotfixes included, they are tracked issues too
- One PR does one thing

## Recommended worktree layout

```text
~/Work/src/.../
  bybit-mantle-arbitrage-monitor/              # primary clone (stays on dev)
  bybit-mantle-arbitrage-monitor-wt/
    whi-101/  # worktree
    whi-105/
    hotfix-…/
```

- Worktree directories go **outside** the primary repo (avoid nested-git confusion)
- The primary repo's `.gitignore` does not need to ignore sibling worktrees

## Forbidden

- Developing an issue in the primary clone's working tree (always use a worktree)
- Pushing feature commits directly to `main` / `dev` (always via PR)
- Force-pushing to `main` / `dev`
- Long-lived giant branches with no PR
- Bundling multiple unrelated issues in one PR
- Leaving the tracker in `In Progress` after the PR opens (must be `In Review`)
- Leaving the tracker un-`Done` after the PR merges
- Continuing work on a branch not based on latest `origin/dev` (drop the worktree,
  re-branch from fresh `dev`)
- Committing `.env`, private keys, or API secrets
- **Deploying from a branch.** The only valid deploy source is a tag — otherwise nobody
  can say which tree production is running
- **Cutting a release to ship one urgent fix.** If `git log origin/main..origin/dev`
  contains anything that must not ship yet, take the hotfix lane
- **Landing a hotfix on `main` without merging `main` back into `dev`.** The next release
  will re-ship the bug you just fixed
- **Squash-merging `release/*` or `hotfix/*` into `main`** (breaks the tag's ancestry with
  `dev` — see [§ Merge strategy](#merge-strategy-per-lane))

## Agent / automation constraints

Implementing agents (including unattended ones) **must**:

1. Change code only inside a worktree — never commit directly to `dev` in the primary clone
2. Move the tracker in lockstep: `In Progress` on start → `In Review` when the PR opens →
   `Done` only after confirming the squash-merge
3. Open the PR against `dev` **unless the issue carries a `hotfix` label**, in which case
   the worktree base is `origin/main` and the PR base is `main` (see [§ Hotfix](#hotfix)).
   **Check the label before creating the worktree** — do not default to `dev` blindly
4. Treat a completed `/implement` three-round review loop (plus the escalation pass when
   round 3 left findings open) as pre-authorization to self-squash-merge, run post-merge
   cleanup, and set the tracker to `Done`. PRs produced any other way — or that skipped
   the loop — stop at `In Review` for a human. **The reviewer must be a context other than
   the implementing one** (`docs/agents/runtime.md`); if the configured reviewer is
   unavailable, the loop did not run and this pre-authorization does not apply — stop at
   `In Review` and say which role was missing
5. Respect module isolation when several issues run in parallel (see
   [§ Parallel issues](#parallel-issues))
6. **Never** self-merge or deploy a change touching **key handling, RPC credentials**, even with a
   green test run — stop at `In Review` for human confirmation. This gate **overrides the
   self-merge pre-authorization in #4.**
7. Never merge `release/*` → `main` without human approval

## Branch protection

Configure server-side protection on `main` and `dev` as soon as the repo's plan allows it:
require a PR, forbid force-pushes, forbid deletion.

> GitHub returns `403 Upgrade to GitHub Pro or make this repository public` for both
> classic branch protection and rulesets on **private repos on the Free plan**. If that
> applies, server-side protection is simply unavailable — the remote is genuinely
> unprotected and workflow discipline is the only real safeguard.

### Fallback: the local `pre-push` hook

While server-side protection is unavailable, `.githooks/pre-push` (checked in) blocks the
most common mistake — pushing straight to `main` or `dev`. Enable it **once per clone and
per worktree**:

```bash
git config core.hooksPath .githooks
```

Deliberate override, when you genuinely need it:

```bash
ALLOW_DIRECT_PUSH=1 git push ...
```

This guards only the local clone. It is **not** a server-side gate.
