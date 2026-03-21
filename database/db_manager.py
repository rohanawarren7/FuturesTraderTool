import sqlite3
import json
import os
from pathlib import Path
from typing import Optional


class DBManager:
    """
    SQLite connection manager for the VWAP Trading Bot.
    All writes go through parameterised queries to prevent SQL injection.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self):
        schema_path = Path(__file__).parent / "schema.sql"
        with self.get_connection() as conn:
            conn.executescript(schema_path.read_text())

    # ------------------------------------------------------------------
    # raw_video_trades
    # ------------------------------------------------------------------

    def insert_video_trade(self, record: dict) -> int:
        """Inserts one extracted trade record. Returns the new row id."""
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO raw_video_trades
                (video_id, trader_name, timestamp_video, timestamp_utc,
                 instrument, direction, entry_trigger, vwap_position,
                 market_state, delta_direction, delta_flip, volume_spike,
                 session_phase, audio_confidence, visual_confidence,
                 outcome, outcome_confidence, outcome_evidence, r_multiple, notes)
                VALUES
                (:video_id, :trader_name, :timestamp_video, :timestamp_utc,
                 :instrument, :direction, :entry_trigger, :vwap_position,
                 :market_state, :delta_direction, :delta_flip, :volume_spike,
                 :session_phase, :audio_confidence, :visual_confidence,
                 :outcome, :outcome_confidence, :outcome_evidence, :r_multiple, :notes)
            """, {
                "video_id":           record.get("video_id"),
                "trader_name":        record.get("trader_name"),
                "timestamp_video":    record.get("timestamp_video"),
                "timestamp_utc":      record.get("timestamp_utc"),
                "instrument":         record.get("instrument"),
                "direction":          record.get("direction"),
                "entry_trigger":      record.get("entry_trigger"),
                "vwap_position":      record.get("vwap_position"),
                "market_state":       record.get("market_state"),
                "delta_direction":    record.get("delta_direction"),
                "delta_flip":         int(record.get("delta_flip", False)),
                "volume_spike":       int(record.get("volume_spike", False)),
                "session_phase":      record.get("session_phase"),
                "audio_confidence":   record.get("audio_confidence", 0.5),
                "visual_confidence":  record.get("visual_confidence", 0.5),
                "outcome":            record.get("outcome", "UNKNOWN"),
                "outcome_confidence": record.get("outcome_confidence", 0.0),
                "outcome_evidence":   record.get("outcome_evidence"),
                "r_multiple":         record.get("r_multiple"),
                "notes":              record.get("notes"),
            })
            return cur.lastrowid

    def get_all_video_trades(self, min_confidence: float = 0.65) -> list[dict]:
        """Returns all video trades at or above the confidence threshold."""
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM raw_video_trades
                WHERE visual_confidence >= ?
                ORDER BY created_at
            """, (min_confidence,)).fetchall()
            return [dict(r) for r in rows]

    def video_already_processed(self, video_id: str) -> bool:
        with self.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM raw_video_trades WHERE video_id = ?",
                (video_id,)
            ).fetchone()[0]
            return count > 0

    # ------------------------------------------------------------------
    # backtest_results
    # ------------------------------------------------------------------

    def insert_backtest_result(self, record: dict) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO backtest_results
                (run_id, prop_firm, account_size, strategy_version,
                 start_date, end_date, instrument, timeframe,
                 total_trades, winning_trades, losing_trades,
                 win_rate, profit_factor, sharpe_ratio, max_drawdown,
                 final_pnl, combine_passed, account_blown, breach_reason,
                 wfe_score, monte_carlo_ruin_pct, params_json)
                VALUES
                (:run_id, :prop_firm, :account_size, :strategy_version,
                 :start_date, :end_date, :instrument, :timeframe,
                 :total_trades, :winning_trades, :losing_trades,
                 :win_rate, :profit_factor, :sharpe_ratio, :max_drawdown,
                 :final_pnl, :combine_passed, :account_blown, :breach_reason,
                 :wfe_score, :monte_carlo_ruin_pct, :params_json)
            """, {
                **record,
                "combine_passed": int(record.get("combine_passed", False)),
                "account_blown":  int(record.get("account_blown", False)),
                "params_json":    json.dumps(record.get("params", {})),
            })
            return cur.lastrowid

    # ------------------------------------------------------------------
    # strategy_params
    # ------------------------------------------------------------------

    def get_latest_strategy_params(self, regime: str = "BALANCED") -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM strategy_params
                WHERE is_active = 1 AND regime = ?
                ORDER BY id DESC LIMIT 1
            """, (regime,)).fetchone()
            return dict(row) if row else None

    def save_strategy_params(self, params: dict) -> int:
        with self.get_connection() as conn:
            # Deactivate existing active params for this regime
            conn.execute(
                "UPDATE strategy_params SET is_active = 0 WHERE regime = ? AND is_active = 1",
                (params.get("regime", "BALANCED"),)
            )
            cur = conn.execute("""
                INSERT INTO strategy_params
                (version, regime, sd_mult_entry, sd_mult_stop, rr_ratio,
                 delta_threshold, volume_threshold, session_start, session_end,
                 max_trades_per_day, valid_from, valid_to, wfe_score, is_active)
                VALUES
                (:version, :regime, :sd_mult_entry, :sd_mult_stop, :rr_ratio,
                 :delta_threshold, :volume_threshold, :session_start, :session_end,
                 :max_trades_per_day, :valid_from, :valid_to, :wfe_score, 1)
            """, params)
            return cur.lastrowid

    # ------------------------------------------------------------------
    # live_trades
    # ------------------------------------------------------------------

    def insert_live_trade(self, record: dict) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT OR IGNORE INTO live_trades
                (trade_id, prop_firm, account_id, instrument, direction,
                 entry_time, exit_time, entry_price, exit_price, contracts,
                 gross_pnl, commission, net_pnl, setup_type,
                 vwap_at_entry, vwap_position, market_state, signal_confidence,
                 stop_price, target_price, r_multiple, tradovate_order_id, notes)
                VALUES
                (:trade_id, :prop_firm, :account_id, :instrument, :direction,
                 :entry_time, :exit_time, :entry_price, :exit_price, :contracts,
                 :gross_pnl, :commission, :net_pnl, :setup_type,
                 :vwap_at_entry, :vwap_position, :market_state, :signal_confidence,
                 :stop_price, :target_price, :r_multiple, :tradovate_order_id, :notes)
            """, record)
            return cur.lastrowid

    def insert_live_trade_from_order(self, order: dict) -> int:
        """
        Maps a Tradovate order dict to the live_trades schema.
        Called by TradovatePoller when a filled order is detected.
        """
        fills = order.get("fills", [{}])
        fill = fills[0] if fills else {}
        record = {
            "trade_id":          str(order.get("id")),
            "prop_firm":         os.getenv("PROP_FIRM", "TOPSTEP_50K"),
            "account_id":        str(order.get("accountId", "")),
            "instrument":        order.get("contractId", ""),
            "direction":         "BUY" if order.get("action") == "Buy" else "SELL",
            "entry_time":        order.get("timestamp"),
            "exit_time":         None,
            "entry_price":       fill.get("price"),
            "exit_price":        None,
            "contracts":         order.get("filledQty", 1),
            "gross_pnl":         None,
            "commission":        fill.get("commission", 0),
            "net_pnl":           None,
            "setup_type":        order.get("text", ""),
            "vwap_at_entry":     None,
            "vwap_position":     None,
            "market_state":      None,
            "signal_confidence": None,
            "stop_price":        None,
            "target_price":      None,
            "r_multiple":        None,
            "tradovate_order_id": order.get("id"),
            "notes":             f"Auto-imported from Tradovate order {order.get('id')}",
        }
        return self.insert_live_trade(record)

    # ------------------------------------------------------------------
    # daily_account_summary
    # ------------------------------------------------------------------

    def upsert_daily_summary(self, record: dict):
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO daily_account_summary
                (date, prop_firm, account_id, opening_balance, closing_balance,
                 daily_pnl, total_trades, winning_trades, mll_floor,
                 peak_eod_balance, winning_days_since_payout, payout_taken, status)
                VALUES
                (:date, :prop_firm, :account_id, :opening_balance, :closing_balance,
                 :daily_pnl, :total_trades, :winning_trades, :mll_floor,
                 :peak_eod_balance, :winning_days_since_payout, :payout_taken, :status)
                ON CONFLICT(date) DO UPDATE SET
                    closing_balance = excluded.closing_balance,
                    daily_pnl = excluded.daily_pnl,
                    total_trades = excluded.total_trades,
                    winning_trades = excluded.winning_trades,
                    mll_floor = excluded.mll_floor,
                    peak_eod_balance = excluded.peak_eod_balance,
                    winning_days_since_payout = excluded.winning_days_since_payout,
                    payout_taken = excluded.payout_taken,
                    status = excluded.status
            """, record)

    def get_recent_live_trades(self, limit: int = 50) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM live_trades
                ORDER BY entry_time DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_daily_summaries(self, limit: int = 30) -> list[dict]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM daily_account_summary
                ORDER BY date DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
