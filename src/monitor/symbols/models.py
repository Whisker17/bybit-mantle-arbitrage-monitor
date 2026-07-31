"""Typed models for the fixed xStock pair inventory (config/pairs.yaml)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]


class RfqMode(StrEnum):
    """M1 conclusion for how M2 should source Fluxion RFQ prices."""

    POLLABLE_QUOTE = "pollable_quote"
    ONCHAIN_FILL_ONLY = "onchain_fill_only"


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
    quote_url: str = Field(min_length=1)
    proxy_quote_url: str = Field(min_length=1)
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


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    low_liquidity: bool
    bybit: BybitSymbol
    fluxion: FluxionSide


class PairsConfig(BaseModel):
    """Root config for the fixed monitor pair list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    inventory_as_of: str = Field(min_length=1)
    low_liquidity_threshold_usd: float = Field(gt=0)
    contracts: Contracts
    rfq: RfqConfig
    pairs: list[Pair] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids_and_symbols(self) -> PairsConfig:
        ids = [p.id for p in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("pair ids must be unique")
        bybit_syms = [p.bybit.symbol for p in self.pairs]
        if len(bybit_syms) != len(set(bybit_syms)):
            raise ValueError("bybit symbols must be unique")
        natives = [p.fluxion.native_token.lower() for p in self.pairs]
        if len(natives) != len(set(natives)):
            raise ValueError("fluxion native_token addresses must be unique")
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
