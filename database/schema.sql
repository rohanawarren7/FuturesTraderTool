-- VWAP Trading Bot — SQLite Schema
-- SQLite (MVP) — upgrade path to PostgreSQL available via db_manager.py

-- Raw video-extracted trades (from AI video analysis pipeline)
CREATE TABLE IF NOT EXISTS raw_video_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    trader_name TEXT,
    timestamp_video REAL,           -- Seconds into video
    timestamp_utc TEXT,             -- Approximate real-world time if known
    instrument TEXT,
    direction TEXT,                 -- BUY / SELL
    entry_trigger TEXT,             -- e.g. MEAN_REVERSION_LONG
    vwap_position TEXT,             -- ABOVE_SD1, BELOW_VWAP etc.
    market_state TEXT,              -- BALANCED / IMBALANCED_BULL etc.
    delta_direction TEXT,
    delta_flip INTEGER,             -- 0 or 1
    volume_spike INTEGER,           -- 0 or 1
    session_phase TEXT,
    audio_confidence REAL,          -- 0-1 confidence from Whisper keyword match
    visual_confidence REAL,         -- 0-1 confidence from Gemini Vision (entry pass)
    outcome TEXT,                   -- WIN / LOSS / UNKNOWN
    outcome_confidence REAL,        -- 0-1 confidence from Gemini Vision (outcome pass)
    outcome_evidence TEXT,          -- Brief explanation from Gemini outcome pass
    r_multiple REAL,                -- Actual R:R achieved if known
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Backtest results with prop firm simulation
CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    prop_firm TEXT,
    account_size INTEGER,
    strategy_version TEXT,
    start_date TEXT,
    end_date TEXT,
    instrument TEXT,
    timeframe TEXT,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    sharpe_ratio REAL,
    max_drawdown REAL,
    final_pnl REAL,
    combine_passed INTEGER,
    account_blown INTEGER,
    breach_reason TEXT,
    wfe_score REAL,
    monte_carlo_ruin_pct REAL,
    params_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Strategy parameters per regime
CREATE TABLE IF NOT EXISTS strategy_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT,
    regime TEXT,
    sd_mult_entry REAL,
    sd_mult_stop REAL,
    rr_ratio REAL,
    delta_threshold INTEGER,
    volume_threshold REAL,
    session_start TEXT,
    session_end TEXT,
    max_trades_per_day INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    wfe_score REAL,
    is_active INTEGER DEFAULT 0
);

-- Live trade execution log
CREATE TABLE IF NOT EXISTS live_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,
    prop_firm TEXT,
    account_id TEXT,
    instrument TEXT,
    direction TEXT,
    entry_time TEXT,
    exit_time TEXT,
    entry_price REAL,
    exit_price REAL,
    contracts INTEGER,
    gross_pnl REAL,
    commission REAL,
    net_pnl REAL,
    setup_type TEXT,
    vwap_at_entry REAL,
    vwap_position TEXT,
    market_state TEXT,
    signal_confidence REAL,
    stop_price REAL,
    target_price REAL,
    r_multiple REAL,
    tradovate_order_id INTEGER,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Daily account summary
CREATE TABLE IF NOT EXISTS daily_account_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    prop_firm TEXT,
    account_id TEXT,
    opening_balance REAL,
    closing_balance REAL,
    daily_pnl REAL,
    total_trades INTEGER,
    winning_trades INTEGER,
    mll_floor REAL,
    peak_eod_balance REAL,
    winning_days_since_payout INTEGER,
    payout_taken REAL DEFAULT 0,
    status TEXT
);

-- Indices for common queries
CREATE INDEX IF NOT EXISTS idx_raw_video_trades_confidence
    ON raw_video_trades (visual_confidence, outcome);

CREATE INDEX IF NOT EXISTS idx_live_trades_entry_time
    ON live_trades (entry_time);

CREATE INDEX IF NOT EXISTS idx_daily_summary_date
    ON daily_account_summary (date, prop_firm);

CREATE INDEX IF NOT EXISTS idx_strategy_params_active
    ON strategy_params (is_active, regime);
