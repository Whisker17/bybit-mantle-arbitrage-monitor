"""Typed models for the Bybit ⇄ Fluxion xStock inventory.

Source of truth: ``config/markets/bybit-fluxion.yaml`` ``inventory:`` block
(M1 body; path moved in M7-2 / WHI-771).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from math import ceil
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]


class RfqMode(StrEnum):
    """M1 conclusion for how M2 should source Fluxion RFQ prices."""

    POLLABLE_QUOTE = "pollable_quote"


class Contracts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chain_id: int = Field(gt=0)
    fluxion_v3_factory: Address
    fluxion_v3_quoter: Address
    fluxion_v3_router: Address
    limit_order_protocol: Address
    usdc: Address
    usdt0: Address


class RfqConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RfqMode
    quote_url: AnyHttpUrl
    proxy_quote_url: AnyHttpUrl
    request_type: Literal["EXACT_INPUT"]
    quote_asset: Literal["USDC"]
    rate_limit_per_minute: int = Field(gt=0)
    min_poll_interval_s: float = Field(gt=0)
    settlement: Literal["limit_order_protocol"]


class BybitSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1)
    base_coin: str = Field(min_length=1)
    # Snapshot from instruments-info; use Decimal to avoid float drift.
    multiplier: Decimal = Field(gt=0)
    multiplier_source: str = Field(min_length=1)

    @field_validator("multiplier", mode="before")
    @classmethod
    def _parse_multiplier(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class AmmPool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # xStock AMM liquidity on Fluxion is V3 wrapper/USDC only at inventory time
    # (no V2 factory published for these pairs — see docs/references/m1-xstocks-inventory.md).
    kind: Literal["v3"]
    pool: Address
    fee: int = Field(gt=0, le=1_000_000)
    # Inventory-time estimate only (2 * USDC balance); not live TVL.
    est_liquidity_usd: float = Field(ge=0)


class FluxionSide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    native_token: Address
    native_decimals: int = Field(ge=0, le=255)
    quote_token: Literal["USDC", "USDT0"]
    quote_token_address: Address
    wrapper_token: Address
    amm: AmmPool | None = None


class SigmaTransitBps(BaseModel):
    """Per-session transit-window σ (bps) for sequential-execution bar (WHI-962).

    Source of record: ``docs/references/m8-delay-decay.md`` (WHI-915), lag=10m
    Bybit mid log-return sample std. Stale-by-design — refresh when WHI-915
    re-runs; not recomputed live.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    open: Decimal = Field(ge=0)
    closed: Decimal = Field(ge=0)

    @field_validator("open", "closed", mode="before")
    @classmethod
    def _parse_sigma(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    low_liquidity: bool
    bybit: BybitSymbol
    fluxion: FluxionSide
    # Bybit Mantle-chain withdrawal fee in **token units** (not USD). Measured
    # via GET /v5/asset/coin/query-info. None = unmeasured → PnL/edge annotate
    # fee_unknown on direction 2 (never silent 0). Liquid AMM pairs must set
    # this (WHI-1090); dust / no-pool inventory stays None until measured.
    asset_withdrawal_fee_tokens: Decimal | None = None
    # WHI-962: session-split transit σ (bps @ ~10 min). None = unmeasured
    # (no sequential bar; panel does not invent a default).
    sigma_transit_bps: SigmaTransitBps | None = None

    @field_validator("asset_withdrawal_fee_tokens", mode="before")
    @classmethod
    def _parse_asset_withdrawal_fee(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value

    @field_validator("asset_withdrawal_fee_tokens")
    @classmethod
    def _nonneg_asset_withdrawal_fee(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("asset_withdrawal_fee_tokens must be >= 0 when set")
        return value


class PairsConfig(BaseModel):
    """Root config for the fixed monitor pair list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    inventory_as_of: date
    low_liquidity_threshold_usd: float = Field(gt=0)
    contracts: Contracts
    rfq: RfqConfig
    pairs: list[Pair] = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> PairsConfig:
        ids = [p.id for p in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("pair ids must be unique")
        bybit_syms = [p.bybit.symbol for p in self.pairs]
        if len(bybit_syms) != len(set(bybit_syms)):
            raise ValueError("bybit symbols must be unique")
        natives = [p.fluxion.native_token.lower() for p in self.pairs]
        if len(natives) != len(set(natives)):
            raise ValueError("fluxion native_token addresses must be unique")

        quote_addrs = {
            "USDC": self.contracts.usdc.lower(),
            "USDT0": self.contracts.usdt0.lower(),
        }
        threshold = self.low_liquidity_threshold_usd
        for pair in self.pairs:
            expected_quote = quote_addrs[pair.fluxion.quote_token]
            if pair.fluxion.quote_token_address.lower() != expected_quote:
                raise ValueError(
                    f"{pair.id}: fluxion.quote_token_address must match contracts."
                    f"{pair.fluxion.quote_token.lower()}"
                )
            expected_low = _expected_low_liquidity(pair, threshold)
            if pair.low_liquidity != expected_low:
                raise ValueError(
                    f"{pair.id}: low_liquidity={pair.low_liquidity} disagrees with "
                    f"rule (no AMM or est_liquidity_usd < {threshold})"
                )
            # Dir2-enabled = liquid AMM. Missing fee is a config gap, not a
            # runtime unknown — the dashboard must not rank an unpriced dir2
            # Net against fully-costed rows (WHI-1090).
            if (
                not pair.low_liquidity
                and pair.fluxion.amm is not None
                and pair.asset_withdrawal_fee_tokens is None
            ):
                raise ValueError(
                    f"{pair.id}: liquid AMM pair requires measured "
                    "asset_withdrawal_fee_tokens (dir2 ineligible otherwise)"
                )

        # Per-pair poll floor so concurrent EXACT_INPUT quotes stay ≤ rate_limit.
        n_pairs = len(self.pairs)
        min_interval = ceil(60 * n_pairs / self.rfq.rate_limit_per_minute)
        if self.rfq.min_poll_interval_s + 1e-9 < min_interval:
            raise ValueError(
                f"rfq.min_poll_interval_s={self.rfq.min_poll_interval_s} is too small "
                f"for {n_pairs} pairs at {self.rfq.rate_limit_per_minute}/min "
                f"(need ≥ {min_interval}s per pair)"
            )
        return self

    def pair_by_id(self, pair_id: str) -> Pair:
        for pair in self.pairs:
            if pair.id == pair_id:
                return pair
        raise KeyError(pair_id)

    def liquid_pairs(self) -> list[Pair]:
        return [p for p in self.pairs if not p.low_liquidity]

    def pairs_with_amm(self) -> list[Pair]:
        return [p for p in self.pairs if p.fluxion.amm is not None]


def _expected_low_liquidity(pair: Pair, threshold_usd: float) -> bool:
    """True when no AMM pool or inventory est TVL is below the LP gate."""
    amm = pair.fluxion.amm
    if amm is None:
        return True
    return amm.est_liquidity_usd < threshold_usd
