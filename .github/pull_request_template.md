## Summary

<!-- What this PR does and why -->

## Base branch

- [ ] `dev` (default: feature / fix / chore) → merge with **squash**
- [ ] `main` (release or hotfix only) → merge with a **merge commit**, never squash

<!-- Targeting `main`? Confirm the lane is right: if `git log origin/main..origin/dev`
holds anything that must not ship yet, this has to be a hotfix off `origin/main`, not a
release. See docs/GIT_WORKFLOW.md § Choosing a promotion lane. -->

## Release / hotfix only

- [ ] Project version bumped to match the tag being created
- [ ] Tag + GitHub Release planned; deploy will come **from the tag**, not a branch
- [ ] Hotfix: `main` will be merged back into `dev` after this lands
- [ ] Tracker Release `commitSha` will be backfilled after tagging

## Type

- [ ] feat
- [ ] fix
- [ ] chore
- [ ] docs
- [ ] hotfix
- [ ] release

## Test plan

- [ ] Local tests run (`uv run pytest` or the relevant subset)
- [ ] If this touches key handling, RPC credentials: verification approach documented (dry-run /
      staging / mocked)
- [ ] No new tunable parameters outside `docs/DESIGN.md` §2, or the deviation is
      explained in the Summary

## Notes

<!-- Risks, rollback plan, follow-up TODOs. Deferred review findings go to
docs/DEFERRED_ISSUES.md in this PR. -->
