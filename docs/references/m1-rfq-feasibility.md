# M1 RFQ feasibility (WHI-730)

Decision recorded for M2 collectors and the TUI RFQ column (`docs/DESIGN.md` §2.2 / §8).

## Conclusion

**Mode: `pollable_quote`**

xChange Atomic RFQ on Fluxion exposes a **public EXACT_INPUT quote API** that can be
polled without trading credentials. M2 should treat RFQ as a live quote column, not
degrade to “last on-chain fill only.”

Config mirror: `config/pairs.yaml` → `rfq.mode: pollable_quote`.

## Endpoints (verified 2026-07-31)

| Role | URL |
|------|-----|
| Preferred same-origin proxy | `POST https://fluxion.network/api/limit-order/quote` |
| Upstream public proxy | `POST https://fluxion-proxy-api-production.up.railway.app/quote` |

No API key required on either path. Upstream is rate-limited to **60 req/min**; do not
poll faster than **once every 5s** per process (Fluxion trade-skill guidance).

### Request

```json
{
  "tokenIn": "0x…",
  "tokenOut": "0x…",
  "amount": "100000000",
  "type": "EXACT_INPUT"
}
```

Rules:

- Counter-asset for RFQ is **USDC** on Mantle (`0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9`).
- `amount` is base units of `tokenIn` (USDC = 6 decimals; native xStocks = 18).
- Exact-output RFQ is not documented.

### Response (shape)

```json
{
  "side": "buy",
  "chainId": 5000,
  "amountIn": "100000000",
  "amountOut": "…",
  "price": "310.63…",
  "requestId": "uuid",
  "tokenIn": "0x…",
  "tokenOut": "0x…",
  "createdAt": "…",
  "rawPrice": "…",
  "rawAmountOut": "…"
}
```

Use `amountOut` / `price` as the executable quote. Treat `raw*` as pre-adjustment
references only. HTTP `204` means no executable RFQ at that moment (not an error for a
resting limit order, but not a displayable mid either).

Smoke test (TSLAx native / 100 USDC buy, 2026-07-31): HTTP 200 with `side=buy`,
`chainId=5000`, price ≈ token mid.

## On-chain settlement / attribution events

RFQ-priced trades settle through the **Fluxion Limit Order Protocol** (1inch-style LOP),
not as Uniswap V3 `Swap` logs on the AMM pool.

| Item | Value |
|------|--------|
| Contract | `0x11de6011345586785810e52448a44c6595eedc18` |
| EIP-712 name / version | `Fluxion Limit Order Protocol` / `4` |
| Chain | Mantle `5000` |

M4 attribution should classify **LOP fills** as MM/RFQ-driven and **V3 pool Swaps** as
AMM/taker-driven. Exact event signatures and decoding land with M2/M4; this issue only
locks the contract address and the mechanism split.

Order build/submit (execution) is out of scope for this read-only panel; URLs exist under
`fluxion.network/api/limit-order/orderbook/v4.1/5000` for completeness.

## Implications for M2

1. Collector: poll RFQ quote for each monitored native xStock ↔ USDC pair (respect rate
   limit); surface stale/204 as “RFQ unavailable,” not as a zero price.
2. AMM path remains independent: Fluxion V3 wrapper/USDC pools via factory
   `0xF883162Ed9c7E8EF604214c964c678E40c9B737C` (UniV3-compatible; fee tiers
   100/500/3000/10000 observed; liquid xStock pools use **3000**).
3. TUI: separate AMM and RFQ columns (DESIGN §1.2 / §2.2) — RFQ column is live quote when
   `mode=pollable_quote`.

## Sources

- Fluxion trade-skill (public): https://github.com/Fluxion-Exchange/Fluxion-trade-skill
  (`references/contracts.md`, `references/xstock-rfq.md`)
- xStocks Assets API: `https://api.backed.fi/api/v1/token` (Mantle deployments + wrappers)
- Bybit instruments-info: `GET /v5/market/instruments-info?category=spot` field
  `xstockMultiplier` / `symbolType=xstocks`
