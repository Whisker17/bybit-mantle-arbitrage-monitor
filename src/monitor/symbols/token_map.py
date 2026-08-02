"""Token address → pair_id maps for collectors / RFQ enrichment (WHI-768)."""

from __future__ import annotations

from monitor.symbols.models import PairsConfig


def native_token_to_pair(pairs: PairsConfig) -> dict[str, str]:
    """Map lowercased native xStock ERC-20 → pair_id (inventory Transfer stream)."""
    return {p.fluxion.native_token.lower(): p.id for p in pairs.pairs}


def inventory_token_to_pair(pairs: PairsConfig) -> dict[str, str]:
    """Native + wrapper tokens → pair_id (RFQ receipt stock mapping)."""
    out: dict[str, str] = {}
    for p in pairs.pairs:
        out[p.fluxion.native_token.lower()] = p.id
        out[p.fluxion.wrapper_token.lower()] = p.id
    return out


def native_token_decimals(pairs: PairsConfig) -> dict[str, int]:
    return {
        p.fluxion.native_token.lower(): p.fluxion.native_decimals for p in pairs.pairs
    }


def quote_is_token0_by_pair(pairs: PairsConfig) -> dict[str, bool]:
    """UniV3 token0 < token1 address order → whether quote (USDC) is token0."""
    out: dict[str, bool] = {}
    for p in pairs.pairs:
        q = p.fluxion.quote_token_address.lower()
        w = p.fluxion.wrapper_token.lower()
        out[p.id] = q < w
    return out
