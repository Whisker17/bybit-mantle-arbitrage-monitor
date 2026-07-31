# Deferred issues registry

A living log of issues that were **surfaced during review but consciously not fixed**
in the PR that found them. This is not a bug tracker for open work — it is the record of
*known, accepted debt*: things we decided to defer, so a future change touching the same
area starts from knowledge instead of rediscovery.

## How to use this file

- **Add an entry** whenever a review turns up a real issue that a PR deliberately leaves
  unfixed (scope, risk, or priority). Record it here in the same PR that defers it.
- **Reference it** before working on the affected area — check whether the thing you are
  about to "discover" is already logged, and whether a listed fix is now in scope.
- **Close an entry** by moving it to *Resolved* (bottom) with the PR/commit that fixed
  it, rather than deleting — the history is useful.
- Keep entries short. Link the originating tracker issue / PR and the code symbol so the
  entry stays findable as the code moves.

Severity is the reviewer's judgement at defer time: **High** (correctness/safety, fix
soon — anything touching key handling, RPC credentials defaults to at least High), **Medium**
(operational/perf, fix when convenient), **Low** (nit/consistency).

## Entry format

```markdown
- **<one-line description of the defect>** (<Severity>, <issue-id>).
  `<file>::<symbol>` — what's wrong, why it was deferred, and what the fix would be.
```

---

## Open

- **RFQ LOP fill topic0 not fill-observed** (Medium, WHI-730 → M2).
  `docs/references/m1-rfq-feasibility.md` — signature-derived 1inch LOP v4 topics are
  locked, but no live `OrderFilled` logs were found on
  `0x11de6011345586785810e52448a44c6595eedc18` in ~200k Mantle blocks at inventory.
  M2 should confirm topic0 + decode layout from a real fill before M4 attribution.

- **Fluxion V2 factory not published for xStocks** (Low, WHI-730 → M2 if needed).
  `config/pairs.yaml` / `AmmPool.kind` — inventory is V3-only; DESIGN still says
  “V2/V3”. Revisit if Fluxion publishes a V2 factory used by xStock pairs.

- **Wrapper→native share conversion not in inventory** (Medium, WHI-730 → M2).
  `config/pairs.yaml` Fluxion AMM is wrapper/USDC V3; Bybit mid is native-token.
  M1 verifies `wrapper.asset() == native` but does not record `convertToAssets` /
  share ratio or wrapper decimals. M2 AMM quotes must convert wrapper mid to
  native-comparable units before edge math.

---

## Resolved

_(none yet)_
