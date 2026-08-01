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

- **RFQ LOP fill topic0 not fill-observed** (Medium, WHI-730 → M2 → M4).
  `docs/references/m1-rfq-feasibility.md` / `monitor.fluxion.abi.TOPIC0_ORDER_FILLED` —
  M2 collectors subscribe with signature-derived 1inch LOP v4 topics and persist
  `fluxion_rfq_fills`, but no live fill was observed at inventory or wiring time.
  Confirm topic0 + decode layout from a real fill before M4 attribution trusts labels.

- **RFQ fill rows lack pair/direction/size/price/taker** (Medium, WHI-731 → M4).
  `fluxion_rfq_fills` stores order_hash + remaining + tx_hash only — `OrderFilled`
  does not encode pair identity. Enrich from receipt Transfer/LOP order bytes when
  a live fill is available; needed for M4 mechanism labels on RFQ legs.

- **Fluxion V2 factory not published for xStocks** (Low, WHI-730 → later if needed).
  `config/pairs.yaml` / `AmmPool.kind` — inventory is V3-only; DESIGN still says
  “V2/V3”. Revisit if Fluxion publishes a V2 factory used by xStock pairs.

---

## Resolved

- **Wrapper→native share conversion not in inventory** (Medium, WHI-730 → M2 / WHI-731).
  `monitor.fluxion.pools.fetch_pool_states` — each block Multicall3 includes
  ERC-4626 `convertToAssets(1e wrapper_decimals)` and stores
  `mid_usdc_per_wrapper`, `mid_usdc_per_native`, `wrapper_assets_per_share` in
  `fluxion_pool_state`.
