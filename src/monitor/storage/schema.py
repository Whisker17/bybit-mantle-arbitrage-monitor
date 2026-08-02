"""Unified SQLite DDL for live collector tables (M2 / WHI-731).

Pure realtime accumulation from process start — no historical backfill.
``gap`` columns flag rows written immediately after a reconnect / missed block
window so downstream metrics can exclude or weight them.
"""

from __future__ import annotations

SCHEMA_VERSION = 6

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
    CREATE INDEX IF NOT EXISTS idx_bybit_book_ts
        ON bybit_book (exchange_ts_ms)
    """,
    """
    CREATE TABLE IF NOT EXISTS bybit_book_1m (
        pair_id             TEXT    NOT NULL,
        bucket_ts_ms        INTEGER NOT NULL,
        symbol              TEXT    NOT NULL,
        bid                 TEXT    NOT NULL,
        ask                 TEXT    NOT NULL,
        bid_de_multiplied   TEXT    NOT NULL,
        ask_de_multiplied   TEXT    NOT NULL,
        multiplier          TEXT    NOT NULL,
        n                   INTEGER NOT NULL,
        PRIMARY KEY (pair_id, bucket_ts_ms)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bybit_book_1m_ts
        ON bybit_book_1m (bucket_ts_ms)
    """,
    """
    CREATE TABLE IF NOT EXISTS bybit_depth (
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
        depth_levels        INTEGER NOT NULL,
        buckets_usd         TEXT    NOT NULL,
        bid_vwap_dm         TEXT    NOT NULL,
        ask_vwap_dm         TEXT    NOT NULL,
        gap                 INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bybit_depth_pair_ts
        ON bybit_depth (pair_id, exchange_ts_ms)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bybit_depth_ts
        ON bybit_depth (exchange_ts_ms)
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
    CREATE INDEX IF NOT EXISTS idx_bybit_trades_ts
        ON bybit_trades (exchange_ts_ms)
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
    CREATE INDEX IF NOT EXISTS idx_fluxion_pool_state_recv
        ON fluxion_pool_state (recv_ts_ms)
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
    CREATE INDEX IF NOT EXISTS idx_fluxion_swaps_pair_recv
        ON fluxion_swaps (pair_id, recv_ts_ms)
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
    CREATE INDEX IF NOT EXISTS idx_fluxion_rfq_quotes_ts
        ON fluxion_rfq_quotes (poll_ts_ms)
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
        -- WHI-768: receipt-enriched fields (nullable until backfill/live enrich).
        pair_id                 TEXT,
        maker                   TEXT,
        taker                   TEXT,
        direction               TEXT,
        making_token            TEXT,
        taking_token            TEXT,
        making_amount           TEXT,
        taking_amount           TEXT,
        usdc_amount             TEXT,
        stock_amount            TEXT,
        enriched                INTEGER NOT NULL DEFAULT 0,
        gap                     INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fluxion_rfq_fills_pair_block
        ON fluxion_rfq_fills (pair_id, block_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fluxion_rfq_fills_maker
        ON fluxion_rfq_fills (maker)
    """,
    """
    CREATE TABLE IF NOT EXISTS erc20_transfers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id         TEXT    NOT NULL,
        token           TEXT    NOT NULL,
        block_number    INTEGER NOT NULL,
        block_ts        INTEGER NOT NULL,
        recv_ts_ms      INTEGER NOT NULL,
        tx_hash         TEXT    NOT NULL,
        log_index       INTEGER NOT NULL,
        frm             TEXT    NOT NULL,
        to_addr         TEXT    NOT NULL,
        amount          TEXT    NOT NULL,
        amount_raw      TEXT    NOT NULL,
        gap             INTEGER NOT NULL DEFAULT 0,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_erc20_transfers_pair_block
        ON erc20_transfers (pair_id, block_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_erc20_transfers_frm
        ON erc20_transfers (frm)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_erc20_transfers_to
        ON erc20_transfers (to_addr)
    """,
    """
    CREATE TABLE IF NOT EXISTS address_labels (
        address             TEXT    PRIMARY KEY,
        label               TEXT    NOT NULL,
        evidence_summary    TEXT    NOT NULL,
        first_seen_ms       INTEGER,
        last_seen_ms        INTEGER,
        source              TEXT    NOT NULL,
        is_rebalancer       INTEGER NOT NULL DEFAULT 0,
        n_rfq_maker         INTEGER NOT NULL DEFAULT 0,
        n_amm               INTEGER NOT NULL DEFAULT 0,
        cex_touch_transfers INTEGER NOT NULL DEFAULT 0,
        updated_at_ms       INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_address_labels_label
        ON address_labels (label)
    """,
    """
    CREATE TABLE IF NOT EXISTS rebalance_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        address         TEXT    NOT NULL,
        counterparty    TEXT    NOT NULL,
        pair_id         TEXT    NOT NULL,
        token           TEXT    NOT NULL,
        amount          TEXT    NOT NULL,
        direction       TEXT    NOT NULL,
        block_number    INTEGER NOT NULL,
        block_ts        INTEGER NOT NULL,
        recv_ts_ms      INTEGER NOT NULL,
        tx_hash         TEXT    NOT NULL,
        log_index       INTEGER NOT NULL,
        UNIQUE (tx_hash, log_index, address)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_rebalance_events_addr_ts
        ON rebalance_events (address, block_ts)
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
    """
    CREATE INDEX IF NOT EXISTS idx_collector_gaps_start
        ON collector_gaps (gap_start_ms)
    """,
    # WHI-778: shared underlying equity reference (keyed by ticker, not pair_id).
    # UNIQUE(ticker, as_of_ms, source) dedups frozen closes under closed-session
    # poll cadence (same publish_time every 5 min).
    """
    CREATE TABLE IF NOT EXISTS underlying_prices (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT    NOT NULL,
        price           TEXT    NOT NULL,
        currency        TEXT    NOT NULL,
        price_type      TEXT    NOT NULL,
        as_of_ms        INTEGER NOT NULL,
        recv_ts_ms      INTEGER NOT NULL,
        source          TEXT    NOT NULL,
        feed_id         TEXT,
        conf            TEXT,
        gap             INTEGER NOT NULL DEFAULT 0,
        UNIQUE (ticker, as_of_ms, source)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_underlying_prices_ticker_asof
        ON underlying_prices (ticker, as_of_ms)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_underlying_prices_recv
        ON underlying_prices (recv_ts_ms)
    """,
    # WHI-777: authoritative CEX rolling 24h quote volume (REST poll).
    """
    CREATE TABLE IF NOT EXISTS cex_volume_24h (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        pair_id             TEXT    NOT NULL,
        symbol              TEXT    NOT NULL,
        poll_ts_ms          INTEGER NOT NULL,
        recv_ts_ms          INTEGER NOT NULL,
        volume_quote_24h    TEXT    NOT NULL,
        trade_count_24h     INTEGER,
        source              TEXT    NOT NULL,
        gap                 INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cex_volume_24h_pair_ts
        ON cex_volume_24h (pair_id, poll_ts_ms)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cex_volume_24h_ts
        ON cex_volume_24h (poll_ts_ms)
    """,
)

# Columns added in v4 to pre-existing fluxion_rfq_fills rows (ALTER path).
RFQ_FILL_V4_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pair_id", "TEXT"),
    ("maker", "TEXT"),
    ("taker", "TEXT"),
    ("direction", "TEXT"),
    ("making_token", "TEXT"),
    ("taking_token", "TEXT"),
    ("making_amount", "TEXT"),
    ("taking_amount", "TEXT"),
    ("usdc_amount", "TEXT"),
    ("stock_amount", "TEXT"),
    ("enriched", "INTEGER NOT NULL DEFAULT 0"),
)
