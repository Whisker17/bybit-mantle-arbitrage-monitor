"""Typed models for the Binance ⇄ PancakeSwap bStocks inventory (M7-3 / WHI-772).

Source of truth: ``config/markets/binance-pancake.yaml`` ``inventory:`` block.
Multiplier field is ``ui_multiplier`` (BEP-677) — multiply semantics, not Bybit divide.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]


class BStocksContracts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chain_id: int = Field(gt=0)
    pancake_v3_factory: Address
    multicall3: Address
    usdt: Address


class BStocksRfqConfig(BaseModel):
    """bStocks v1 is AMM-only (M7-1); mode is always ``none``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["none"]


class BinanceSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1)
    base_coin: str = Field(min_length=1)
    # BEP-677 uiMultiplier as float string (1.0 = no adjustment).
    ui_multiplier: Decimal = Field(gt=0)
    ui_multiplier_source: str = Field(min_length=1)

    @field_validator("ui_multiplier", mode="before")
    @classmethod
    def _parse_mult(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class PancakeAmmPool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["v3"]
    pool: Address
    fee: int = Field(gt=0, le=1_000_000)
    est_liquidity_usd: float = Field(ge=0)


class PancakeSide(BaseModel):
    """On-chain BEP-20 + optional PCS V3 USDT pool.

    ``amm is None`` means **dex:none** for the monitor: CEX legs still run;
    the chain poller skips the pair via ``pairs_with_amm()`` (WHI-790).
    The native token address is still recorded for the BEP-20 registry even
    when no in-scope pool exists (V2-only / WBNB-only / no pool).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    native_token: Address
    native_decimals: int = Field(ge=0, le=255)
    quote_token: Literal["USDT"]
    quote_token_address: Address
    amm: PancakeAmmPool | None = None


class BStocksPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    low_liquidity: bool
    binance: BinanceSymbol
    pancake: PancakeSide

    def has_amm(self) -> bool:
        """True when a collector-scope PCS V3 USDT pool is configured."""
        return self.pancake.amm is not None

    def dex_mode(self) -> Literal["amm", "none"]:
        """Product label: ``amm`` vs ``none`` (no in-scope DEX pool)."""
        return "amm" if self.has_amm() else "none"


class BStocksPairsConfig(BaseModel):
    """Root config for the full bStocks monitor pair list (WHI-790: all Binance bases)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    inventory_as_of: date
    low_liquidity_threshold_usd: float = Field(gt=0)
    contracts: BStocksContracts
    rfq: BStocksRfqConfig
    pairs: list[BStocksPair] = Field(min_length=1)

    @model_validator(mode="after")
    def _invariants(self) -> BStocksPairsConfig:
        ids = [p.id for p in self.pairs]
        if len(ids) != len(set(ids)):
            raise ValueError("pair ids must be unique")
        symbols = [p.binance.symbol for p in self.pairs]
        if len(symbols) != len(set(symbols)):
            raise ValueError("binance symbols must be unique")
        natives = [p.pancake.native_token.lower() for p in self.pairs]
        if len(natives) != len(set(natives)):
            raise ValueError("pancake native_token addresses must be unique")

        usdt = self.contracts.usdt.lower()
        threshold = self.low_liquidity_threshold_usd
        for pair in self.pairs:
            if pair.pancake.quote_token_address.lower() != usdt:
                raise ValueError(
                    f"{pair.id}: pancake.quote_token_address must match contracts.usdt"
                )
            expected_low = _expected_low_liquidity(pair, threshold)
            if pair.low_liquidity != expected_low:
                raise ValueError(
                    f"{pair.id}: low_liquidity={pair.low_liquidity} disagrees with "
                    f"rule (no AMM or est_liquidity_usd < {threshold})"
                )
        return self

    def pair_by_id(self, pair_id: str) -> BStocksPair:
        for pair in self.pairs:
            if pair.id == pair_id:
                return pair
        raise KeyError(pair_id)

    def liquid_pairs(self) -> list[BStocksPair]:
        return [p for p in self.pairs if not p.low_liquidity]

    def pairs_with_amm(self) -> list[BStocksPair]:
        """Pairs with a collector-scope AMM pool (skips dex:none)."""
        return [p for p in self.pairs if p.has_amm()]

    def pairs_dex_none(self) -> list[BStocksPair]:
        """CEX-only pairs — no in-scope PCS V3 USDT pool (WHI-790)."""
        return [p for p in self.pairs if not p.has_amm()]


def _expected_low_liquidity(pair: BStocksPair, threshold_usd: float) -> bool:
    amm = pair.pancake.amm
    if amm is None:
        return True
    return amm.est_liquidity_usd < threshold_usd
