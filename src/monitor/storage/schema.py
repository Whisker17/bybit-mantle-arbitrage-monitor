"""Unified SQLite DDL for live collector tables (M2 / WHI-731).

Pure realtime accumulation from process start — no historical backfill.
``gap`` columns flag rows written immediately after a reconnect / missed block
window so downstream metrics can exclude or weight them.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bybit_book (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id             TEXT    NOT NULL,
        symbol              TEXT    NOT NULL,
        exchange_ts_ms      INTEGER NOT NULL,
        recv_ts_ms          INTEGER NOT NULL,
        bid                 TEXT    NOT NULL,
        ask                 TEXT    NOT NULL,
        bid_de_multiplied   TEXT    NOT NULL,
        ask_de_multiplied   TEXT    NOT NULL,
        multiplier          TEXT    NOT NULL,
        gap                 INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bybit_book_pair_ts
        ON bybit_book (pair_id, exchange_ts_ms)
    """,
    """
    CREATE TABLE IF NOT EXISTS bybit_trades (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id             TEXT    NOT NULL,
        symbol              TEXT    NOT NULL,
        exchange_ts_ms      INTEGER NOT NULL,
        recv_ts_ms          INTEGER NOT NULL,
        trade_id            TEXT    NOT NULL,
        price               TEXT    NOT NULL,
        price_de_multiplied TEXT    NOT NULL,
        size                TEXT    NOT NULL,
        side                TEXT    NOT NULL,
        multiplier          TEXT    NOT NULL,
        gap                 INTEGER NOT NULL DEFAULT 0,
        UNIQUE (symbol, trade_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bybit_trades_pair_ts
        ON bybit_trades (pair_id, exchange_ts_ms)
    """,
    """
    CREATE TABLE IF NOT EXISTS fluxion_pool_state (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id                 TEXT    NOT NULL,
        pool                    TEXT    NOT NULL,
        block_number            INTEGER NOT NULL,
        block_ts                INTEGER NOT NULL,
        recv_ts_ms              INTEGER NOT NULL,
        sqrt_price_x96          TEXT    NOT NULL,
        tick                    INTEGER NOT NULL,
        liquidity               TEXT    NOT NULL,
        token0                  TEXT    NOT NULL,
        token1                  TEXT    NOT NULL,
        mid_usdc_per_wrapper    TEXT    NOT NULL,
        mid_usdc_per_native     TEXT    NOT NULL,
        wrapper_assets_per_share TEXT   NOT NULL,
        gap                     INTEGER NOT NULL DEFAULT 0,
        UNIQUE (pool, block_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fluxion_pool_state_pair_block
        ON fluxion_pool_state (pair_id, block_number)
    """,
    """
    CREATE TABLE IF NOT EXISTS fluxion_swaps (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id                 TEXT    NOT NULL,
        pool                    TEXT    NOT NULL,
        block_number            INTEGER NOT NULL,
        block_ts                INTEGER NOT NULL,
        recv_ts_ms              INTEGER NOT NULL,
        tx_hash                 TEXT    NOT NULL,
        log_index               INTEGER NOT NULL,
        sender                  TEXT    NOT NULL,
        recipient               TEXT    NOT NULL,
        amount0                 TEXT    NOT NULL,
        amount1                 TEXT    NOT NULL,
        sqrt_price_x96          TEXT    NOT NULL,
        liquidity               TEXT    NOT NULL,
        tick                    INTEGER NOT NULL,
        amount_token0           TEXT    NOT NULL,
        amount_token1           TEXT    NOT NULL,
        direction               TEXT    NOT NULL,
        price_usdc_per_wrapper  TEXT,
        gas_used                INTEGER,
        effective_gas_price     INTEGER,
        gap                     INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fluxion_swaps_pair_block
        ON fluxion_swaps (pair_id, block_number)
    """,
    """
    CREATE TABLE IF NOT EXISTS fluxion_rfq_quotes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id         TEXT    NOT NULL,
        poll_ts_ms      INTEGER NOT NULL,
        recv_ts_ms      INTEGER NOT NULL,
        token_in        TEXT    NOT NULL,
        token_out       TEXT    NOT NULL,
        amount_in       TEXT    NOT NULL,
        amount_out      TEXT,
        price           TEXT,
        side            TEXT,
        request_id      TEXT,
        http_status     INTEGER NOT NULL,
        available       INTEGER NOT NULL,
        gap             INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fluxion_rfq_quotes_pair_ts
        ON fluxion_rfq_quotes (pair_id, poll_ts_ms)
    """,
    """
    CREATE TABLE IF NOT EXISTS fluxion_rfq_fills (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        block_number            INTEGER NOT NULL,
        block_ts                INTEGER NOT NULL,
        recv_ts_ms              INTEGER NOT NULL,
        tx_hash                 TEXT    NOT NULL,
        log_index               INTEGER NOT NULL,
        order_hash              TEXT    NOT NULL,
        remaining_making_amount TEXT    NOT NULL,
        gap                     INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collector_gaps (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source          TEXT    NOT NULL,
        gap_start_ms    INTEGER NOT NULL,
        gap_end_ms      INTEGER NOT NULL,
        detail          TEXT    NOT NULL
    )
    """,
)
