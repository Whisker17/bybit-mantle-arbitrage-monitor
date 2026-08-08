"""Typed market file schema (config/markets/{id}.yaml) — M7-2 / WHI-771."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MultiplierSemantics(StrEnum):
    """How CEX display prices relate to on-chain / raw token mids.

    * ``divide`` — Bybit xStocks: comparable = cex_price / multiplier
      (``de_multiplied_price``).
    * ``multiply`` — Binance bStocks BEP-677: amm_raw ≈ cex_display * ui_multiplier
      (see docs/references/m7-bstocks-inventory.md). Do **not** reuse
      ``de_multiplied_price`` for this market.
    """

    DIVIDE = "divide"
    MULTIPLY = "multiply"


class CexSide(BaseModel):
    """Centralized exchange leg of a market."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    venue: str = Field(min_length=1)
    quote_asset: str = Field(min_length=1)
    multiplier_semantics: MultiplierSemantics
    # Optional REST/WS endpoint hints (geo-safe defaults live in collector.yaml).
    rest_base_url: str | None = None
    ws_base_url: str | None = None


class DexSide(BaseModel):
    """On-chain DEX / AMM leg of a market."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    venue: str = Field(min_length=1)
    chain_id: int = Field(gt=0)
    pool_kind: str = Field(min_length=1)
    quote_asset: str = Field(min_length=1)
    # ERC-20 decimals of the quote token in the pool (USDC=6 Mantle; USDT=18 BSC).
    # Injected into AmmPoolState at tick lift (WHI-773) — not hardcoded in metrics.
    quote_decimals: int = Field(default=6, ge=0, le=255)
    # True when the market has a pollable RFQ / quote vendor (Fluxion).
    has_rfq: bool = False


class MarketCosts(BaseModel):
    """Venue fee / gas knobs consumed by metrics + PnL v2 assembly.

    Mapped onto ``MetricsConfig`` field names at load time so pure edge/PnL
    math stays unchanged (``bybit_taker_fee_bps`` etc. remain the algorithm
    surface; the market file is the parameterized source).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # CEX taker fee in bps (Bybit xStocks Adventure Zone 20; Binance spot 10).
    cex_taker_fee_bps: Decimal = Field(ge=0)
    # One AMM swap gas in USD (Mantle ~$0.01; BSC differs — set per market).
    gas_usd_per_swap: Decimal = Field(ge=0)
    # Signed USDC premium over USDT in bps (positive = USDC richer). 0 when CEX
    # and DEX share the same quote (e.g. Binance USDT ⇄ Pancake USDT). Engine
    # applies sign by direction (WHI-960); not constrained to >= 0.
    quote_basis_bps: Decimal = Field(default=Decimal(0))

    @field_validator(
        "cex_taker_fee_bps",
        "gas_usd_per_swap",
        "quote_basis_bps",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, value: object) -> object:
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class MarketFile(BaseModel):
    """Root of ``config/markets/{id}.yaml``.

    ``inventory`` is the venue-specific pair list (Bybit/Fluxion shape for
    bybit-fluxion; Binance/Pancake shape for binance-pancake). Validation of
    inventory body is delegated to the venue loader so algorithms keep their
    existing ``PairsConfig`` types for the live Bybit path.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=2)
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    cex: CexSide
    dex: DexSide
    costs: MarketCosts
    # WHI-821: optional per-market PnL quote age annotate threshold (ms).
    # When set, overrides api.yaml ``quote_max_age_ms`` for this market only.
    # Quiet CEX books (event-driven) need looser values on closed-session markets.
    quote_max_age_ms: int | None = Field(default=None, ge=1_000)
    # Opaque inventory mapping — validated by market-specific loaders.
    inventory: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def _id_shape(self) -> MarketFile:
        mid = self.id.strip().lower()
        if mid != self.id or "_" in self.id:
            raise ValueError(
                f"market id must be lower-case hyphenated, got {self.id!r}"
            )
        if not self.inventory:
            raise ValueError("inventory must be a non-empty mapping")
        return self
