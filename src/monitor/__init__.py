"""Phase-2 live panel: Bybit spot ⇄ Fluxion (Mantle) tokenized stocks.

All new product code for the realtime TUI lands under this package.
Phase-1 offline backtest remains in ``mba`` and is not extended here.

Module map and the phase-1 → phase-2 reuse list live in ``docs/DESIGN.md`` §4.2.
"""

from monitor.symbols import load_pairs_config

__all__ = ["load_pairs_config"]
