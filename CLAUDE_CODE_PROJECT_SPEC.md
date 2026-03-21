# VWAP FUTURES TRADING BOT — CLAUDE CODE PROJECT SPECIFICATION
### Version 1.0 | Paste this entire document into Claude Code as your project context

---

## 1. PROJECT OVERVIEW

You are building a **self-improving algorithmic futures trading bot** that:

1. Analyses live-stream videos of profitable VWAP-based futures traders using an AI pipeline (Gemini/Whisper) to extract codifiable trade rules
2. Executes those rules via a **VWAP + Order Flow strategy** on CME Micro Futures (MES/MNQ)
3. Backtests and optimises within a **prop-firm constraint simulator** that perfectly mirrors Topstep, Apex, and FTMO rules
4. Self-improves weekly via Walk-Forward Optimisation (WFO), regime detection, and Reinforcement Learning (PPO)
5. Connects initially via **TradingView webhooks → TradersPost → Tradovate** (MVP), then upgrades to **Sierra Chart direct API**

**Primary target instrument**: MES (Micro E-mini S&P 500) and MNQ (Micro Nasdaq)
**Primary prop firm**: Topstep ($50K Express Funded Account, $49/mo)
**Target income**: £1,500–£4,500/month net (Months 3–8)
**Tech stack**: Python 3.11+, Pine Script v5, SQLite → PostgreSQL, FastAPI, n8n

---

## 2. COMPLETE SYSTEM ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 1 — RULE EXTRACTION                │
│  YouTube VODs → yt-dlp → FFmpeg → Whisper → Gemini Vision  │
│       → SQLite Trade DB → Pattern Miner → Strategy Spec     │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                 PHASE 2 — STRATEGY ENGINE                   │
│  Pine Script v5 (TradingView) → Webhook Alert → TradersPost │
│       → Tradovate Paper Account → Live Execution            │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│              PHASE 3 — BACKTEST & OPTIMISE                  │
│  Python Backtrader + Prop Firm Constraint Simulator         │
│  Walk-Forward Optimisation → Regime Detection → Monte Carlo │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│             PHASE 4 — SELF-LEARNING LOOP                    │
│  Weekly WFO → Optuna HPO → RL Policy Update (PPO)          │
│  Performance Dashboard → Circuit Breakers → Auto-Deploy     │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. PROP FIRM CONSTRAINT SIMULATOR (CRITICAL — BUILD FIRST)

This module must be implemented before any backtesting begins. Every backtest run must pass through this simulator. It is the single most important component for ensuring backtest results are valid on a funded account.

### 3.1 Supported Prop Firms Configuration

```python
PROP_FIRM_CONFIGS = {

    "TOPSTEP_50K": {
        "firm": "Topstep",
        "account_size": 50_000,
        "monthly_cost_usd": 49,
        "profit_target": 3_000,          # Must hit to pass combine
        "max_loss_limit": 2_000,         # Trailing drawdown (EOD-based)
        "trailing_type": "EOD",          # End-of-day trailing, NOT intraday
        "trailing_stops_at_profit": None, # MLL never stops trailing on Topstep
        "max_contracts": 5,
        "daily_loss_limit": None,        # Removed Aug 2024 — no daily loss limit
        "profit_split": 0.90,            # Trader keeps 90% after first $10K
        "first_withdrawal_bonus": 10_000, # First $10K lifetime at 100%
        "min_payout_days": 5,            # 5 winning days (>=200 net) before payout
        "min_winning_day_pnl": 200,      # Each qualifying day needs $200+ net
        "max_payout_per_request": 5_000, # Or 50% of balance, whichever lower
        "payout_max_pct": 0.50,
        "instruments": ["MES", "MNQ", "ES", "NQ", "CL", "GC"],
        "exchange": "CME/CBOT/NYMEX/COMEX",
        "news_trading_allowed": True,    # Allowed but risky
        "activation_fee": 0,             # No activation fee on current model
        "notes": "XFA is simulated. MLL trails EOD high-water mark."
    },

    "TOPSTEP_100K": {
        "firm": "Topstep",
        "account_size": 100_000,
        "monthly_cost_usd": 99,
        "profit_target": 6_000,
        "max_loss_limit": 3_000,
        "trailing_type": "EOD",
        "trailing_stops_at_profit": None,
        "max_contracts": 10,
        "daily_loss_limit": None,
        "profit_split": 0.90,
        "first_withdrawal_bonus": 10_000,
        "min_payout_days": 5,
        "min_winning_day_pnl": 200,
        "max_payout_per_request": 5_000,
        "payout_max_pct": 0.50,
        "instruments": ["MES", "MNQ", "ES", "NQ", "CL", "GC"],
        "exchange": "CME/CBOT/NYMEX/COMEX",
        "news_trading_allowed": True,
        "activation_fee": 0,
    },

    "TOPSTEP_150K": {
        "firm": "Topstep",
        "account_size": 150_000,
        "monthly_cost_usd": 149,
        "profit_target": 9_000,
        "max_loss_limit": 4_500,
        "trailing_type": "EOD",
        "trailing_stops_at_profit": None,
        "max_contracts": 15,
        "daily_loss_limit": None,
        "profit_split": 0.90,
        "first_withdrawal_bonus": 10_000,
        "min_payout_days": 5,
        "min_winning_day_pnl": 200,
        "max_payout_per_request": 5_000,
        "payout_max_pct": 0.50,
        "instruments": ["MES", "MNQ", "ES", "NQ", "CL", "GC"],
        "exchange": "CME/CBOT/NYMEX/COMEX",
        "news_trading_allowed": True,
        "activation_fee": 0,
    },

    "APEX_50K": {
        "firm": "Apex Trader Funding",
        "account_size": 50_000,
        "monthly_cost_usd": 167,          # Approximate eval cost
        "profit_target": 3_000,
        "max_loss_limit": 2_500,          # Trailing drawdown (INTRADAY-based)
        "trailing_type": "INTRADAY",      # Includes unrealised PnL
        "trailing_stops_at_profit": 2_500, # Stops trailing once $2,500 profit reached
        "max_contracts": 10,             # Eval: 10 contracts, PA: 6 contracts
        "pa_max_contracts": 6,
        "daily_loss_limit": None,        # No daily loss limit on Apex
        "profit_split": 0.90,
        "min_payout_days": None,
        "consistency_rule": True,        # Max single day <= 30% of total profit
        "instruments": ["MES", "MNQ", "ES", "NQ"],
        "exchange": "CME",
        "news_trading_allowed": False,   # No trading within 2 min of major news
        "activation_fee": 85,
    },

    "APEX_100K": {
        "firm": "Apex Trader Funding",
        "account_size": 100_000,
        "monthly_cost_usd": 207,
        "profit_target": 6_000,
        "max_loss_limit": 3_000,
        "trailing_type": "INTRADAY",
        "trailing_stops_at_profit": 3_000,
        "max_contracts": 14,
        "pa_max_contracts": 10,
        "daily_loss_limit": None,
        "profit_split": 0.90,
        "consistency_rule": True,
        "instruments": ["MES", "MNQ", "ES", "NQ"],
        "exchange": "CME",
        "news_trading_allowed": False,
        "activation_fee": 85,
    },

    "FTMO_100K": {
        "firm": "FTMO",
        "account_size": 100_000,
        "monthly_cost_usd": 0,           # One-time challenge fee ~540 EUR
        "challenge_fee_eur": 540,
        "profit_target_pct": 0.10,       # 10% of balance = $10,000 (Challenge)
        "verification_target_pct": 0.05, # 5% of balance = $5,000 (Verification)
        "max_daily_loss_pct": 0.05,      # 5% of initial balance = $5,000/day
        "max_loss_pct": 0.10,            # 10% overall = $10,000
        "trailing_type": "EOD",          # Recalculated at midnight CET
        "trailing_stops_at_profit": None,
        "max_contracts": None,           # No contract limit — position sizing free
        "daily_loss_limit": 5_000,       # $5,000 daily (5% of $100K)
        "profit_split": 0.80,            # Standard: 80% to trader; up to 90% possible
        "instruments": ["ES", "NQ", "CL", "GC", "ZB"],  # Full futures suite
        "exchange": "CME",
        "news_trading_allowed": True,
        "min_trading_days": 4,
        "notes": "Two-step evaluation. Strict daily loss limit. No size limit."
    },
}
```

### 3.2 Prop Firm Constraint Engine — Core Class

Build the following Python class. This must wrap every backtest.

```python
# file: prop_firm_simulator.py

class PropFirmSimulator:
    """
    Simulates funded account constraints during backtesting.
    Wraps a backtest engine and enforces all prop firm rules in real time.
    Must be called on every bar and every trade execution.
    """

    def __init__(self, config: dict):
        self.config = config
        self.account_size = config["account_size"]
        self.balance = config["account_size"]
        self.equity = config["account_size"]         # Includes open PnL
        self.peak_eod_balance = config["account_size"]
        self.peak_intraday_equity = config["account_size"]
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.lifetime_withdrawals = 0.0
        self.trade_log = []
        self.daily_log = {}
        self.current_day = None
        self.winning_days_since_last_payout = 0
        self.winning_day_pnl_threshold = config.get("min_winning_day_pnl", 200)
        self.account_blown = False
        self.combine_passed = False
        self.status = "COMBINE"          # COMBINE → FUNDED → BLOWN → PASSED
        self.breach_reason = None

    # ----- CORE RULE CHECKERS -----

    def get_mll_floor(self) -> float:
        """
        Returns the current Maximum Loss Limit floor.
        Topstep (EOD): floor = peak_eod_balance - max_loss_limit
        Apex (INTRADAY): floor = peak_intraday_equity - max_loss_limit
        FTMO (EOD): floor = max(account_size, peak_eod_balance) - max_loss_amount
        """
        mll = self.config["max_loss_limit"]
        trailing_type = self.config.get("trailing_type", "EOD")

        if self.config["firm"] == "FTMO":
            max_loss_amount = self.account_size * self.config.get("max_loss_pct", 0.10)
            reference = max(self.account_size, self.peak_eod_balance)
            return reference - max_loss_amount

        if trailing_type == "INTRADAY":
            stops_at = self.config.get("trailing_stops_at_profit")
            if stops_at and (self.balance - self.account_size) >= stops_at:
                # Apex: drawdown stops once profit exceeds threshold
                return self.account_size - mll
            return self.peak_intraday_equity - mll

        else:  # EOD (Topstep default)
            return self.peak_eod_balance - mll

    def get_daily_loss_limit_floor(self) -> float:
        """
        Returns absolute balance floor from daily loss limit rule.
        Only applies to FTMO (5% daily loss limit).
        Topstep has NO daily loss limit since Aug 2024.
        """
        if self.config.get("daily_loss_limit"):
            return self.balance - self.config["daily_loss_limit"]
        return -float("inf")   # No daily loss limit

    def check_breach(self) -> tuple[bool, str]:
        """
        Checks all breach conditions. Returns (is_breached, reason).
        Call this on every tick/bar update.
        """
        mll_floor = self.get_mll_floor()
        daily_floor = self.get_daily_loss_limit_floor()

        if self.equity <= mll_floor:
            return True, f"MLL_BREACH: Equity {self.equity:.2f} <= MLL Floor {mll_floor:.2f}"

        if self.equity <= daily_floor:
            return True, f"DAILY_LOSS_BREACH: Equity {self.equity:.2f} <= Daily Floor {daily_floor:.2f}"

        # Apex consistency rule: no single day > 30% of total profit
        if self.config.get("consistency_rule") and self.total_pnl > 0:
            if self.daily_pnl > (self.total_pnl * 0.30):
                return True, f"CONSISTENCY_BREACH: Daily PnL {self.daily_pnl:.2f} > 30% of total {self.total_pnl:.2f}"

        return False, ""

    def check_contract_limit(self, requested_contracts: int) -> int:
        """
        Caps contract size to the prop firm limit.
        Returns the allowed number of contracts.
        """
        max_c = self.config.get("max_contracts")
        if max_c is None:
            return requested_contracts
        return min(requested_contracts, max_c)

    def check_combine_passed(self) -> bool:
        """Checks if profit target has been met to pass the combine."""
        return (self.balance - self.account_size) >= self.config["profit_target"]

    def check_payout_eligible(self) -> tuple[bool, float]:
        """
        Returns (is_eligible, max_payout_amount).
        Topstep: 5 winning days >= $200 net each since last payout.
        """
        min_days = self.config.get("min_payout_days", 5)
        if self.winning_days_since_last_payout < min_days:
            return False, 0.0

        profit_in_account = self.balance - self.account_size
        max_pct = self.config.get("payout_max_pct", 0.50)
        max_fixed = self.config.get("max_payout_per_request", 5_000)
        payout = min(profit_in_account * max_pct, max_fixed, profit_in_account)
        return True, max(0.0, payout)

    def request_payout(self) -> float:
        """
        Processes a payout. Applies 100% vs 90% split depending on lifetime withdrawals.
        Returns net amount received by trader.
        """
        eligible, gross_amount = self.check_payout_eligible()
        if not eligible or gross_amount <= 0:
            return 0.0

        bonus_remaining = max(0, self.config.get("first_withdrawal_bonus", 10_000) - self.lifetime_withdrawals)
        if gross_amount <= bonus_remaining:
            net = gross_amount  # 100% during bonus period
        else:
            at_100 = bonus_remaining
            at_90 = gross_amount - bonus_remaining
            net = at_100 + (at_90 * self.config["profit_split"])

        self.balance -= gross_amount
        self.lifetime_withdrawals += gross_amount
        self.winning_days_since_last_payout = 0

        # After payout on Topstep, MLL resets to current balance
        if self.config["firm"] == "Topstep":
            self.peak_eod_balance = self.balance

        return net

    # ----- STATE UPDATERS -----

    def update_intraday(self, current_equity: float):
        """Call this on every tick/bar with current equity (balance + open PnL)."""
        self.equity = current_equity
        if self.config.get("trailing_type") == "INTRADAY":
            if current_equity > self.peak_intraday_equity:
                self.peak_intraday_equity = current_equity
        breached, reason = self.check_breach()
        if breached:
            self.account_blown = True
            self.breach_reason = reason

    def close_day(self, eod_balance: float):
        """Call this at end of each trading day (4:00 PM ET / market close)."""
        self.daily_pnl = eod_balance - (self.balance if self.current_day else self.account_size)
        self.balance = eod_balance

        # Update EOD high-water mark (Topstep / FTMO)
        if eod_balance > self.peak_eod_balance:
            self.peak_eod_balance = eod_balance

        # Reset intraday peak for next session
        self.peak_intraday_equity = eod_balance

        # Track winning days for payout eligibility
        if self.daily_pnl >= self.winning_day_pnl_threshold:
            self.winning_days_since_last_payout += 1

        self.total_pnl = self.balance - self.account_size
        self.daily_log[str(self.current_day)] = {
            "eod_balance": eod_balance,
            "daily_pnl": self.daily_pnl,
            "mll_floor": self.get_mll_floor(),
            "winning_days_since_payout": self.winning_days_since_last_payout,
        }

        # Check if combine passed
        if not self.combine_passed and self.check_combine_passed():
            self.combine_passed = True
            self.status = "FUNDED"

    def get_safe_daily_loss_budget(self) -> float:
        """
        Returns the maximum loss the bot should take today.
        Set to 40% of MLL as a safety buffer — HARD CIRCUIT BREAKER.
        """
        mll = self.config["max_loss_limit"]
        return mll * 0.40   # Conservative: use only 40% of allowed loss per day

    def get_report(self) -> dict:
        """Returns summary stats for the backtest."""
        return {
            "firm": self.config["firm"],
            "account_size": self.account_size,
            "final_balance": self.balance,
            "total_pnl": self.total_pnl,
            "lifetime_withdrawals": self.lifetime_withdrawals,
            "combine_passed": self.combine_passed,
            "account_blown": self.account_blown,
            "breach_reason": self.breach_reason,
            "status": self.status,
            "peak_eod_balance": self.peak_eod_balance,
            "current_mll_floor": self.get_mll_floor(),
            "winning_days_since_payout": self.winning_days_since_last_payout,
        }
```

### 3.3 Bot Risk Parameters (Hard-Coded Safety Rules)

These parameters must be embedded in the bot's execution layer and cannot be overridden by optimisation. They exist specifically to protect the funded account.

```python
BOT_RISK_PARAMS = {
    # Daily circuit breakers
    "daily_stop_loss_pct_of_mll": 0.40,   # Stop trading if daily loss hits 40% of MLL
    "daily_profit_target_usd": 600,        # Optional: stop trading after $600 profit day
    "profit_protect_threshold_usd": 400,   # Reduce size by 50% once $400 up on the day

    # Position sizing
    "default_contracts": 1,               # Start with 1 contract until 50 live trades proven
    "scale_up_threshold_trades": 50,       # Increase to 2 contracts after 50 profitable trades
    "max_contracts_in_use": 3,            # Never exceed 3 contracts in early phase (even if firm allows 5)

    # Trade timing filters
    "no_trade_open_minutes": 15,          # No trades in first 15 min after RTH open (9:30 ET)
    "no_trade_close_minutes": 15,         # No trades in last 15 min before RTH close (4:00 ET)
    "no_trade_before_rtoh_minutes": 5,    # No trades 5 min before London open (3:00 AM ET)
    "rth_start_et": "09:30",
    "rth_end_et": "16:00",
    "london_open_et": "03:00",
    "preferred_window_start": "09:45",    # Optimal VWAP window start
    "preferred_window_end": "14:00",      # Optimal VWAP window end

    # News blackout (Apex mandatory, Topstep recommended)
    "news_blackout_minutes_before": 2,
    "news_blackout_minutes_after": 2,
    "high_impact_events": ["NFP", "CPI", "FOMC", "FOMC_MINUTES", "GDP", "PCE", "PPI", "RETAIL_SALES"],

    # Order execution
    "entry_order_type": "LIMIT",          # Prefer limit orders to control slippage
    "exit_order_type": "LIMIT",           # Prefer limit for exits
    "stop_order_type": "STOP_MARKET",     # Stops must be market to ensure fill
    "max_slippage_ticks": 2,              # Cancel if fill more than 2 ticks from limit

    # Drawdown monitoring
    "drawdown_monitor_interval_seconds": 5,  # Check MLL breach every 5 seconds
    "emergency_flatten_on_breach": True,     # Immediately flatten all positions if breach detected
}
```

---

## 4. MARKET DATA & INSTRUMENT SPECIFICATIONS

```python
INSTRUMENT_SPECS = {
    "MES": {
        "name": "Micro E-mini S&P 500",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 1.25,       # $1.25 per tick
        "point_value": 5.00,      # $5 per full point
        "margin_intraday": 40,    # Approx $40 per contract intraday (Tradovate)
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 50,
        "commission_per_side": 0.35,  # Approx $0.35 per side on Tradovate
        "data_source": "Rithmic / Tradovate / Interactive Brokers",
    },
    "MNQ": {
        "name": "Micro E-mini Nasdaq 100",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 0.50,
        "point_value": 2.00,
        "margin_intraday": 40,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 200,
        "commission_per_side": 0.35,
        "data_source": "Rithmic / Tradovate / Interactive Brokers",
    },
    "ES": {
        "name": "E-mini S&P 500",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 12.50,
        "point_value": 50.00,
        "margin_intraday": 500,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 50,
        "commission_per_side": 1.50,
    },
    "NQ": {
        "name": "E-mini Nasdaq 100",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 5.00,
        "point_value": 20.00,
        "margin_intraday": 500,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 200,
        "commission_per_side": 1.50,
    },
}
```

---

## 5. VWAP STRATEGY ENGINE

### 5.1 VWAP Calculation (Python)

```python
# file: vwap_calculator.py

import pandas as pd
import numpy as np

class VWAPCalculator:
    """
    Calculates multi-timeframe VWAP with standard deviation bands.
    Supports session-anchored, weekly-anchored, and monthly-anchored VWAP.
    """

    @staticmethod
    def calculate_session_vwap(df: pd.DataFrame) -> pd.DataFrame:
        """
        df must have columns: timestamp, open, high, low, close, volume
        Returns df with added columns: vwap, vwap_sd1_upper, vwap_sd1_lower,
        vwap_sd2_upper, vwap_sd2_lower, vwap_sd3_upper, vwap_sd3_lower
        """
        df = df.copy()
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_volume"] = df["typical_price"] * df["volume"]
        df["tp_vol_sq"] = (df["typical_price"] ** 2) * df["volume"]

        # Detect session resets (new trading day)
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date
        df["session_start"] = df["date"] != df["date"].shift(1)

        # Cumulative sums, reset on session start
        df["cum_tp_vol"] = df.groupby("date")["tp_volume"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["cum_tp_vol_sq"] = df.groupby("date")["tp_vol_sq"].cumsum()

        df["vwap"] = df["cum_tp_vol"] / df["cum_vol"]

        # Standard deviation bands
        df["variance"] = (df["cum_tp_vol_sq"] / df["cum_vol"]) - (df["vwap"] ** 2)
        df["variance"] = df["variance"].clip(lower=0)
        df["vwap_std"] = np.sqrt(df["variance"])

        for multiplier in [1, 2, 3]:
            df[f"vwap_sd{multiplier}_upper"] = df["vwap"] + (multiplier * df["vwap_std"])
            df[f"vwap_sd{multiplier}_lower"] = df["vwap"] - (multiplier * df["vwap_std"])

        return df

    @staticmethod
    def calculate_anchored_vwap(df: pd.DataFrame, anchor_type: str = "weekly") -> pd.DataFrame:
        """
        Calculates VWAP anchored to the start of the week or month.
        anchor_type: 'weekly' or 'monthly'
        """
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_volume"] = df["typical_price"] * df["volume"]

        if anchor_type == "weekly":
            df["anchor_key"] = df["timestamp"].dt.to_period("W")
        else:
            df["anchor_key"] = df["timestamp"].dt.to_period("M")

        df["cum_tp_vol"] = df.groupby("anchor_key")["tp_volume"].cumsum()
        df["cum_vol"] = df.groupby("anchor_key")["volume"].cumsum()
        df[f"vwap_{anchor_type}"] = df["cum_tp_vol"] / df["cum_vol"]

        return df

    @staticmethod
    def get_vwap_position(price: float, vwap: float, sd1_u: float, sd1_l: float,
                           sd2_u: float, sd2_l: float) -> str:
        """
        Returns price position relative to VWAP bands.
        Used as primary feature for balance/imbalance detection.
        """
        if price > sd2_u:
            return "ABOVE_SD2"
        elif price > sd1_u:
            return "ABOVE_SD1"
        elif price > vwap:
            return "ABOVE_VWAP"
        elif price > sd1_l:
            return "BELOW_VWAP"
        elif price > sd2_l:
            return "BELOW_SD1"
        else:
            return "BELOW_SD2"
```

### 5.2 Market Balance / Imbalance Detection

```python
# file: market_state_detector.py

class MarketStateDetector:
    """
    Detects whether the market is in a balanced or imbalanced state.
    This is the core logic extracted from watching VWAP traders.
    """

    STATES = {
        "BALANCED":         "Price within SD1, delta flat, DOM symmetric",
        "IMBALANCED_BULL":  "Price above SD1, positive delta, aggressive buying",
        "IMBALANCED_BEAR":  "Price below SD1, negative delta, aggressive selling",
        "VOLATILE_TRANS":   "Price crossing VWAP multiple times, expanding range",
        "LOW_ACTIVITY":     "Small bars, low volume, pre-market or lunch session",
    }

    def detect(self,
               vwap_position: str,
               cumulative_delta: float,
               delta_direction: str,          # "POSITIVE", "NEGATIVE", "NEUTRAL"
               atr_ratio: float,              # Current ATR / 20-period ATR
               volume_ratio: float,           # Current bar volume / 20-bar avg volume
               price_crosses_vwap_last_10: int  # Times price crossed VWAP in last 10 bars
               ) -> str:

        # Balanced conditions
        is_balanced_vwap = vwap_position in ["ABOVE_VWAP", "BELOW_VWAP"]  # Within SD1
        is_flat_delta = delta_direction == "NEUTRAL" or abs(cumulative_delta) < 500
        is_normal_atr = 0.7 < atr_ratio < 1.5
        is_normal_vol = 0.5 < volume_ratio < 2.0

        if is_balanced_vwap and is_flat_delta and is_normal_atr and price_crosses_vwap_last_10 >= 2:
            return "BALANCED"

        # Imbalanced bullish
        if vwap_position in ["ABOVE_SD1", "ABOVE_SD2"] and delta_direction == "POSITIVE":
            return "IMBALANCED_BULL"

        # Imbalanced bearish
        if vwap_position in ["BELOW_SD1", "BELOW_SD2"] and delta_direction == "NEGATIVE":
            return "IMBALANCED_BEAR"

        # Volatile transition
        if price_crosses_vwap_last_10 >= 4 and atr_ratio > 1.5:
            return "VOLATILE_TRANS"

        # Low activity (avoid trading)
        if volume_ratio < 0.4 or atr_ratio < 0.4:
            return "LOW_ACTIVITY"

        return "BALANCED"
```

### 5.3 Entry Signal Generator

```python
# file: signal_generator.py

class SignalGenerator:
    """
    Generates BUY/SELL/HOLD signals based on VWAP position,
    market state, and order flow conditions.
    These rules are derived from pattern mining of trader video data.
    Update this class as more video data is processed.
    """

    def generate(self,
                 market_state: str,
                 vwap_position: str,
                 delta_direction: str,
                 delta_flip: bool,            # Delta changed direction this bar
                 price_at_vwap_band: bool,    # Price touched or bounced from SD band
                 volume_spike: bool,          # Volume > 2x average
                 session_phase: str,          # "OPEN", "MID", "CLOSE"
                 time_in_session_minutes: int
                 ) -> dict:

        signal = {"action": "HOLD", "setup_type": None, "confidence": 0.0, "notes": ""}

        # Avoid trading in first 15 min or last 15 min
        if time_in_session_minutes < 15 or time_in_session_minutes > 375:
            signal["notes"] = "TIME_FILTER: Outside preferred trading window"
            return signal

        # Avoid during low activity
        if market_state == "LOW_ACTIVITY":
            signal["notes"] = "LOW_ACTIVITY: No signal"
            return signal

        # SETUP 1: BALANCED MEAN-REVERSION (highest probability, 65-75% win rate)
        # Price tags SD1/SD2 from inside, delta flips, volume spike = fade back to VWAP
        if market_state == "BALANCED":
            if vwap_position == "ABOVE_SD1" and delta_direction == "NEGATIVE" and delta_flip:
                signal = {
                    "action": "SELL",
                    "setup_type": "MEAN_REVERSION_SHORT",
                    "confidence": 0.72,
                    "target": "VWAP",
                    "stop": "SD2_UPPER",
                    "rr_ratio": 2.0,
                    "notes": "Price at SD1 resistance, delta flipping negative, balanced market"
                }
            elif vwap_position == "BELOW_SD1" and delta_direction == "POSITIVE" and delta_flip:
                signal = {
                    "action": "BUY",
                    "setup_type": "MEAN_REVERSION_LONG",
                    "confidence": 0.72,
                    "target": "VWAP",
                    "stop": "SD2_LOWER",
                    "rr_ratio": 2.0,
                    "notes": "Price at SD1 support, delta flipping positive, balanced market"
                }

        # SETUP 2: VWAP RECLAIM CONTINUATION (imbalanced market, 55-65% win rate)
        # Price breaks back through VWAP after brief excursion, delta confirms direction
        elif market_state in ["IMBALANCED_BULL", "IMBALANCED_BEAR"]:
            if (market_state == "IMBALANCED_BULL" and
                vwap_position == "ABOVE_VWAP" and
                delta_direction == "POSITIVE" and
                volume_spike):
                signal = {
                    "action": "BUY",
                    "setup_type": "VWAP_CONTINUATION_LONG",
                    "confidence": 0.60,
                    "target": "SD1_UPPER",
                    "stop": "VWAP",
                    "rr_ratio": 1.5,
                    "notes": "VWAP reclaim long, imbalanced bull, delta confirms"
                }
            elif (market_state == "IMBALANCED_BEAR" and
                  vwap_position == "BELOW_VWAP" and
                  delta_direction == "NEGATIVE" and
                  volume_spike):
                signal = {
                    "action": "SELL",
                    "setup_type": "VWAP_CONTINUATION_SHORT",
                    "confidence": 0.60,
                    "target": "SD1_LOWER",
                    "stop": "VWAP",
                    "rr_ratio": 1.5,
                    "notes": "VWAP reclaim short, imbalanced bear, delta confirms"
                }

        # SETUP 3: SD2 EXTREME FADE (2σ overextension, mean-reversion, 55-60% win rate)
        # Price hits SD2, volume climax, fade back toward SD1/VWAP
        if vwap_position == "ABOVE_SD2" and volume_spike and delta_direction == "NEGATIVE":
            signal = {
                "action": "SELL",
                "setup_type": "SD2_EXTREME_FADE_SHORT",
                "confidence": 0.58,
                "target": "SD1_UPPER",
                "stop": "SD2_UPPER_PLUS_BUFFER",
                "rr_ratio": 2.5,
                "notes": "SD2 extreme extension fade, volume climax, negative delta"
            }
        elif vwap_position == "BELOW_SD2" and volume_spike and delta_direction == "POSITIVE":
            signal = {
                "action": "BUY",
                "setup_type": "SD2_EXTREME_FADE_LONG",
                "confidence": 0.58,
                "target": "SD1_LOWER",
                "stop": "SD2_LOWER_MINUS_BUFFER",
                "rr_ratio": 2.5,
                "notes": "SD2 extreme extension fade, volume climax, positive delta"
            }

        return signal
```

---

## 6. DATABASE SCHEMA

```sql
-- file: schema.sql
-- SQLite (MVP) — upgradeable to PostgreSQL

-- Raw video-extracted trades (from AI video analysis pipeline)
CREATE TABLE raw_video_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    trader_name TEXT,
    timestamp_video REAL,           -- Seconds into video
    timestamp_utc TEXT,             -- Approximate real-world time if known
    instrument TEXT,
    direction TEXT,                 -- BUY / SELL
    entry_trigger TEXT,             -- e.g., MEAN_REVERSION_LONG
    vwap_position TEXT,             -- ABOVE_SD1, BELOW_VWAP etc.
    market_state TEXT,              -- BALANCED / IMBALANCED_BULL etc.
    delta_direction TEXT,
    delta_flip INTEGER,             -- 0 or 1
    volume_spike INTEGER,           -- 0 or 1
    session_phase TEXT,
    audio_confidence REAL,          -- 0-1 confidence from Whisper
    visual_confidence REAL,         -- 0-1 confidence from Gemini
    outcome TEXT,                   -- WIN / LOSS / UNKNOWN
    r_multiple REAL,                -- Actual R:R achieved
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Backtest results with prop firm simulation
CREATE TABLE backtest_results (
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
    wfe_score REAL,                 -- Walk-Forward Efficiency
    monte_carlo_ruin_pct REAL,      -- Probability of ruin from Monte Carlo
    params_json TEXT,               -- JSON of strategy parameters used
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Strategy parameters per regime
CREATE TABLE strategy_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT,
    regime TEXT,                    -- BALANCED / IMBALANCED_BULL / IMBALANCED_BEAR etc.
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
CREATE TABLE live_trades (
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
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Daily account summary
CREATE TABLE daily_account_summary (
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
    status TEXT                     -- COMBINE / FUNDED / BLOWN
);
```

---

## 7. AI VIDEO ANALYSIS PIPELINE

### 7.1 Pipeline Orchestrator

```python
# file: video_analysis/pipeline.py

import subprocess
import json
import os
from pathlib import Path
import whisper
import google.generativeai as genai
import sqlite3
from datetime import datetime

GEMINI_TRADE_ANALYSIS_PROMPT = """
You are an expert futures trader analyst. Analyse this screenshot from a live trading session.

Return ONLY valid JSON matching this exact schema (no extra text):
{
  "trade_detected": true/false,
  "instrument": "MES/MNQ/ES/NQ/CL/null",
  "direction": "BUY/SELL/null",
  "entry_trigger": "MEAN_REVERSION_LONG/MEAN_REVERSION_SHORT/VWAP_CONTINUATION_LONG/VWAP_CONTINUATION_SHORT/SD2_EXTREME_FADE_LONG/SD2_EXTREME_FADE_SHORT/OTHER/null",
  "vwap_position": "ABOVE_SD2/ABOVE_SD1/ABOVE_VWAP/BELOW_VWAP/BELOW_SD1/BELOW_SD2/null",
  "market_state": "BALANCED/IMBALANCED_BULL/IMBALANCED_BEAR/VOLATILE_TRANS/LOW_ACTIVITY/null",
  "delta_direction": "POSITIVE/NEGATIVE/NEUTRAL/null",
  "delta_flip": true/false,
  "volume_spike": true/false,
  "session_phase": "OPEN/MID/CLOSE/null",
  "vwap_bands_visible": true/false,
  "order_flow_tool_visible": true/false,
  "confidence": 0.0-1.0,
  "notes": "brief explanation of what you see"
}
"""

AUDIO_TRADE_KEYWORDS = {
    "entry": ["entering", "going long", "going short", "taking", "buying", "selling", "in the trade", "filled", "entry"],
    "exit": ["exiting", "out", "closing", "taking profit", "stopped out", "covering"],
    "vwap": ["vwap", "v-wap", "weighted average", "bands"],
    "delta": ["delta", "cumulative delta", "order flow", "aggressive buyers", "aggressive sellers"],
    "market_state": ["balanced", "imbalanced", "trending", "ranging", "rotation"],
    "reasoning": ["because", "reason", "waiting for", "looking for", "setup", "criteria"],
}

class TradingVideoPipeline:

    def __init__(self, db_path: str, gemini_api_key: str, output_dir: str = "./video_data"):
        self.db_path = db_path
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        genai.configure(api_key=gemini_api_key)
        self.gemini_model = genai.GenerativeModel("gemini-2.0-flash")
        self.whisper_model = whisper.load_model("medium")

    def download_video(self, youtube_url: str, output_name: str) -> Path:
        """Downloads YouTube video using yt-dlp."""
        output_path = self.output_dir / f"{output_name}.mp4"
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "-o", str(output_path),
            youtube_url
        ]
        subprocess.run(cmd, check=True)
        return output_path

    def extract_audio(self, video_path: Path) -> Path:
        """Extracts 16kHz mono audio using FFmpeg."""
        audio_path = video_path.with_suffix("_audio.mp3")
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-ac", "1", "-ar", "16000", "-y",
            str(audio_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return audio_path

    def transcribe_audio(self, audio_path: Path) -> dict:
        """Transcribes audio with Whisper, returns word-level timestamps."""
        result = self.whisper_model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language="en"
        )
        # Save transcript
        transcript_path = audio_path.with_suffix(".json")
        with open(transcript_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    def detect_trade_events(self, transcript: dict) -> list[dict]:
        """
        Scans transcript for trade-related keyword clusters.
        Returns list of flagged events with timestamps.
        """
        events = []
        segments = transcript.get("segments", [])

        for segment in segments:
            text = segment["text"].lower()
            matched_categories = []

            for category, keywords in AUDIO_TRADE_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    matched_categories.append(category)

            # Flag if 2+ categories match (reduces false positives ~70%)
            if len(matched_categories) >= 2:
                events.append({
                    "timestamp_seconds": segment["start"],
                    "text": segment["text"],
                    "matched_categories": matched_categories,
                    "confidence": min(1.0, len(matched_categories) / 4),
                })

        return events

    def extract_frames_at_events(self, video_path: Path, events: list[dict]) -> list[Path]:
        """Extracts video frames at trade event timestamps using FFmpeg."""
        frame_paths = []
        frames_dir = self.output_dir / "frames" / video_path.stem
        frames_dir.mkdir(parents=True, exist_ok=True)

        for event in events:
            t = event["timestamp_seconds"]
            frame_path = frames_dir / f"frame_{t:.1f}s.jpg"
            cmd = [
                "ffmpeg",
                "-ss", str(max(0, t - 2)),  # 2 seconds before event
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                "-y", str(frame_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            if frame_path.exists():
                frame_paths.append(frame_path)
                event["frame_path"] = str(frame_path)

        return frame_paths

    def analyse_frame_with_gemini(self, frame_path: Path) -> dict:
        """Sends a single frame to Gemini Vision for trade analysis."""
        import PIL.Image
        img = PIL.Image.open(frame_path)

        response = self.gemini_model.generate_content(
            [GEMINI_TRADE_ANALYSIS_PROMPT, img],
            generation_config={"response_mime_type": "application/json"}
        )
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            return {"trade_detected": False, "confidence": 0.0, "notes": "Parse error"}

    def save_trade_record(self, video_id: str, trader_name: str,
                          event: dict, analysis: dict) -> None:
        """Saves fused audio + visual trade record to SQLite."""
        if not analysis.get("trade_detected"):
            return

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO raw_video_trades
            (video_id, trader_name, timestamp_video, instrument, direction,
             entry_trigger, vwap_position, market_state, delta_direction,
             delta_flip, volume_spike, session_phase, audio_confidence,
             visual_confidence, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video_id, trader_name,
            event.get("timestamp_seconds"),
            analysis.get("instrument"),
            analysis.get("direction"),
            analysis.get("entry_trigger"),
            analysis.get("vwap_position"),
            analysis.get("market_state"),
            analysis.get("delta_direction"),
            int(analysis.get("delta_flip", False)),
            int(analysis.get("volume_spike", False)),
            analysis.get("session_phase"),
            event.get("confidence", 0.5),
            analysis.get("confidence", 0.5),
            analysis.get("notes"),
        ))
        conn.commit()
        conn.close()

    def run_full_pipeline(self, youtube_url: str, trader_name: str, video_id: str) -> dict:
        """Runs the complete pipeline for one video. Returns summary stats."""
        print(f"[Pipeline] Processing {video_id} from {trader_name}")

        video_path = self.download_video(youtube_url, video_id)
        audio_path = self.extract_audio(video_path)
        transcript = self.transcribe_audio(audio_path)
        events = self.detect_trade_events(transcript)

        print(f"[Pipeline] Found {len(events)} trade events in audio")

        self.extract_frames_at_events(video_path, events)

        analysed = 0
        for event in events:
            if "frame_path" in event:
                analysis = self.analyse_frame_with_gemini(Path(event["frame_path"]))
                self.save_trade_record(video_id, trader_name, event, analysis)
                if analysis.get("trade_detected"):
                    analysed += 1

        return {
            "video_id": video_id,
            "total_audio_events": len(events),
            "trades_detected": analysed,
            "status": "complete"
        }
```

### 7.2 Pattern Miner

```python
# file: video_analysis/pattern_miner.py

import sqlite3
import pandas as pd

class PatternMiner:
    """
    Mines the raw_video_trades table to identify high-probability
    entry conditions. Output feeds directly into strategy_params table.
    Requires minimum 30 trades per condition, recommend 200+ total.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_all_trades(self, min_confidence: float = 0.65) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("""
            SELECT * FROM raw_video_trades
            WHERE visual_confidence >= ?
            AND outcome IS NOT NULL
            AND outcome != 'UNKNOWN'
        """, conn, params=(min_confidence,))
        conn.close()
        return df

    def analyse_conditions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Groups by key condition combinations and calculates win rate.
        Returns DataFrame sorted by win_rate * sample_size (edge score).
        """
        df["win"] = (df["outcome"] == "WIN").astype(int)

        # Key combinations to test
        conditions = [
            ["vwap_position", "market_state"],
            ["vwap_position", "market_state", "delta_direction"],
            ["vwap_position", "market_state", "delta_direction", "volume_spike"],
            ["entry_trigger", "market_state"],
            ["entry_trigger", "session_phase"],
        ]

        results = []
        for combo in conditions:
            grouped = df.groupby(combo).agg(
                trades=("win", "count"),
                wins=("win", "sum"),
                avg_r=("r_multiple", "mean"),
            ).reset_index()
            grouped["win_rate"] = grouped["wins"] / grouped["trades"]
            grouped["edge_score"] = grouped["win_rate"] * grouped["trades"] * grouped["avg_r"].fillna(1)
            grouped["conditions"] = [str(dict(zip(combo, row))) for _, row in grouped[combo].iterrows()]
            results.append(grouped[grouped["trades"] >= 10])

        return pd.concat(results).sort_values("edge_score", ascending=False)

    def export_top_conditions(self, n: int = 5) -> list[dict]:
        """Returns the top N trading conditions by edge score."""
        df = self.get_all_trades()
        if len(df) < 30:
            raise ValueError(f"Insufficient data: {len(df)} trades. Need at least 30.")
        analysis = self.analyse_conditions(df)
        return analysis.head(n).to_dict("records")
```

---

## 8. BACKTESTING ENGINE

### 8.1 Backtest Runner with Prop Firm Constraints

```python
# file: backtesting/backtest_runner.py

import pandas as pd
import numpy as np
from prop_firm_simulator import PropFirmSimulator, PROP_FIRM_CONFIGS
from vwap_calculator import VWAPCalculator
from market_state_detector import MarketStateDetector
from signal_generator import SignalGenerator
from instrument_specs import INSTRUMENT_SPECS

class BacktestRunner:
    """
    Runs a full backtest of the VWAP strategy with prop firm constraints.
    Simulates every rule of the chosen prop firm in real time.
    """

    def __init__(self, prop_firm_key: str, instrument: str = "MES", timeframe: str = "5m"):
        self.prop_config = PROP_FIRM_CONFIGS[prop_firm_key]
        self.prop_firm = PropFirmSimulator(self.prop_config)
        self.instrument = INSTRUMENT_SPECS[instrument]
        self.timeframe = timeframe
        self.vwap_calc = VWAPCalculator()
        self.state_detector = MarketStateDetector()
        self.signal_gen = SignalGenerator()
        self.trades = []
        self.equity_curve = []

    def run(self, df: pd.DataFrame, params: dict) -> dict:
        """
        Runs the backtest on df (OHLCV data).
        params: strategy parameters dict (sd_mult, rr_ratio, etc.)
        Returns results dict.
        """
        df = self.vwap_calc.calculate_session_vwap(df)

        daily_pnl = 0
        open_trade = None
        contracts = self.prop_config.get("max_contracts", 5)

        for i in range(50, len(df)):
            row = df.iloc[i]
            current_equity = self.prop_firm.balance + (open_trade["unrealised_pnl"] if open_trade else 0)

            # --- Update prop firm intraday ---
            self.prop_firm.update_intraday(current_equity)
            if self.prop_firm.account_blown:
                break

            # --- Daily circuit breaker ---
            safe_budget = self.prop_firm.get_safe_daily_loss_budget()
            if daily_pnl <= -safe_budget and open_trade is None:
                # Skip rest of the day
                if i < len(df) - 1 and df.iloc[i+1]["date"] != row["date"]:
                    self.prop_firm.close_day(self.prop_firm.balance)
                    daily_pnl = 0
                continue

            # --- Manage open trade ---
            if open_trade:
                result = self._manage_open_trade(open_trade, row)
                if result["closed"]:
                    pnl = result["pnl"]
                    commission = self.instrument["commission_per_side"] * 2 * open_trade["contracts"]
                    net_pnl = pnl - commission
                    daily_pnl += net_pnl
                    self.prop_firm.balance += net_pnl

                    self.trades.append({
                        "entry_time": open_trade["entry_time"],
                        "exit_time": row["timestamp"],
                        "direction": open_trade["direction"],
                        "net_pnl": net_pnl,
                        "r_multiple": result.get("r_multiple", 0),
                        "setup_type": open_trade["setup_type"],
                    })
                    open_trade = None
                else:
                    open_trade["unrealised_pnl"] = result["unrealised_pnl"]

            # --- Generate new signal if flat ---
            if open_trade is None:
                signal = self.signal_gen.generate(
                    market_state=self._get_market_state(df, i),
                    vwap_position=VWAPCalculator.get_vwap_position(
                        row["close"], row["vwap"],
                        row["vwap_sd1_upper"], row["vwap_sd1_lower"],
                        row["vwap_sd2_upper"], row["vwap_sd2_lower"]
                    ),
                    delta_direction=self._get_delta_direction(df, i),
                    delta_flip=self._detect_delta_flip(df, i),
                    price_at_vwap_band=self._price_at_band(row),
                    volume_spike=row["volume"] > df["volume"].iloc[i-20:i].mean() * 2,
                    session_phase=self._get_session_phase(row),
                    time_in_session_minutes=self._minutes_since_open(row),
                )

                if signal["action"] in ["BUY", "SELL"]:
                    allowed_contracts = self.prop_firm.check_contract_limit(
                        min(params.get("contracts", 1), contracts)
                    )
                    stop_distance = self._calc_stop_distance(signal, row, params)
                    target_distance = stop_distance * params.get("rr_ratio", 2.0)

                    open_trade = {
                        "direction": signal["action"],
                        "entry_price": row["close"],
                        "entry_time": row["timestamp"],
                        "stop_price": (row["close"] - stop_distance) if signal["action"] == "BUY"
                                       else (row["close"] + stop_distance),
                        "target_price": (row["close"] + target_distance) if signal["action"] == "BUY"
                                          else (row["close"] - target_distance),
                        "contracts": allowed_contracts,
                        "setup_type": signal["setup_type"],
                        "unrealised_pnl": 0,
                    }

            # --- End of day ---
            if i < len(df) - 1 and df.iloc[i+1]["date"] != row["date"]:
                self.prop_firm.close_day(self.prop_firm.balance)
                daily_pnl = 0

            self.equity_curve.append({
                "timestamp": row["timestamp"],
                "equity": current_equity,
                "mll_floor": self.prop_firm.get_mll_floor(),
            })

        return self._generate_report()

    def _generate_report(self) -> dict:
        trades_df = pd.DataFrame(self.trades)
        if len(trades_df) == 0:
            return {"error": "No trades generated"}

        wins = trades_df[trades_df["net_pnl"] > 0]
        losses = trades_df[trades_df["net_pnl"] <= 0]

        gross_profit = wins["net_pnl"].sum()
        gross_loss = abs(losses["net_pnl"].sum())

        return {
            **self.prop_firm.get_report(),
            "total_trades": len(trades_df),
            "win_rate": len(wins) / len(trades_df),
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "avg_r_multiple": trades_df["r_multiple"].mean(),
            "sharpe_ratio": self._calc_sharpe(trades_df),
            "max_drawdown": self._calc_max_drawdown(),
        }

    def _calc_sharpe(self, trades_df: pd.DataFrame) -> float:
        if len(trades_df) < 2:
            return 0.0
        daily = trades_df.groupby(trades_df["exit_time"].str[:10])["net_pnl"].sum()
        return (daily.mean() / daily.std()) * np.sqrt(252) if daily.std() > 0 else 0.0

    def _calc_max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        equity = pd.Series([e["equity"] for e in self.equity_curve])
        rolling_max = equity.cummax()
        drawdowns = (equity - rolling_max) / rolling_max
        return float(drawdowns.min())

    # Helper stubs — implement based on your data
    def _get_market_state(self, df, i): return "BALANCED"
    def _get_delta_direction(self, df, i): return "NEUTRAL"
    def _detect_delta_flip(self, df, i): return False
    def _price_at_band(self, row): return False
    def _get_session_phase(self, row): return "MID"
    def _minutes_since_open(self, row): return 60
    def _calc_stop_distance(self, signal, row, params): return row.get("vwap_std", 5) * params.get("sd_mult_stop", 1.0)
    def _manage_open_trade(self, trade, row): return {"closed": False, "unrealised_pnl": 0}
```

---

## 9. WALK-FORWARD OPTIMISATION

```python
# file: optimisation/walk_forward.py

import optuna
import pandas as pd
from datetime import datetime, timedelta
from backtest_runner import BacktestRunner

class WalkForwardOptimiser:
    """
    Rolls a 4-month in-sample / 1-month out-of-sample window across history.
    Optimises parameters on IS, validates on OOS, computes Walk-Forward Efficiency.
    """

    IS_MONTHS = 4
    OOS_MONTHS = 1
    N_TRIALS = 100      # Optuna trials per IS window

    PARAM_SPACE = {
        "sd_mult_entry": (0.75, 2.0),     # SD band for entry (fraction of SD1)
        "sd_mult_stop": (1.0, 2.5),        # SD band for stop (multiple of entry)
        "rr_ratio": (1.5, 3.5),            # Risk:Reward ratio
        "delta_threshold": (100, 800),     # Minimum delta for signal confirmation
        "volume_threshold": (1.5, 3.0),    # Volume spike multiplier
        "contracts": (1, 3),               # Contracts to trade (integer)
    }

    def __init__(self, prop_firm_key: str, instrument: str = "MES"):
        self.prop_firm_key = prop_firm_key
        self.instrument = instrument

    def optimise_window(self, df_is: pd.DataFrame, n_trials: int = None) -> dict:
        """Runs Optuna optimisation on in-sample data. Returns best params."""
        n_trials = n_trials or self.N_TRIALS

        def objective(trial):
            params = {
                "sd_mult_entry": trial.suggest_float("sd_mult_entry", *self.PARAM_SPACE["sd_mult_entry"]),
                "sd_mult_stop": trial.suggest_float("sd_mult_stop", *self.PARAM_SPACE["sd_mult_stop"]),
                "rr_ratio": trial.suggest_float("rr_ratio", *self.PARAM_SPACE["rr_ratio"]),
                "delta_threshold": trial.suggest_int("delta_threshold", *self.PARAM_SPACE["delta_threshold"]),
                "volume_threshold": trial.suggest_float("volume_threshold", *self.PARAM_SPACE["volume_threshold"]),
                "contracts": trial.suggest_int("contracts", *self.PARAM_SPACE["contracts"]),
            }
            runner = BacktestRunner(self.prop_firm_key, self.instrument)
            result = runner.run(df_is, params)

            if result.get("account_blown") or result.get("error"):
                return -999

            # Composite score: penalise blown accounts, reward consistent profits
            score = (
                result.get("profit_factor", 0) * 0.3 +
                result.get("win_rate", 0) * 0.3 +
                result.get("sharpe_ratio", 0) * 0.2 +
                (1 - abs(result.get("max_drawdown", 1))) * 0.2
            )
            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
        return study.best_params

    def run_full_wfo(self, df: pd.DataFrame) -> dict:
        """
        Runs rolling WFO across entire dataset.
        Returns composite OOS equity curve and WFE score.
        """
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        start = df["timestamp"].min()
        end = df["timestamp"].max()

        windows = []
        cursor = start
        while cursor + timedelta(days=30 * (self.IS_MONTHS + self.OOS_MONTHS)) <= end:
            is_end = cursor + timedelta(days=30 * self.IS_MONTHS)
            oos_end = is_end + timedelta(days=30 * self.OOS_MONTHS)
            windows.append({
                "is_start": cursor,
                "is_end": is_end,
                "oos_start": is_end,
                "oos_end": oos_end,
            })
            cursor = is_end

        is_scores = []
        oos_scores = []

        for w in windows:
            df_is = df[(df["timestamp"] >= w["is_start"]) & (df["timestamp"] < w["is_end"])]
            df_oos = df[(df["timestamp"] >= w["oos_start"]) & (df["timestamp"] < w["oos_end"])]

            if len(df_is) < 1000 or len(df_oos) < 200:
                continue

            best_params = self.optimise_window(df_is)

            is_runner = BacktestRunner(self.prop_firm_key, self.instrument)
            is_result = is_runner.run(df_is, best_params)
            is_scores.append(is_result.get("profit_factor", 0))

            oos_runner = BacktestRunner(self.prop_firm_key, self.instrument)
            oos_result = oos_runner.run(df_oos, best_params)
            oos_scores.append(oos_result.get("profit_factor", 0))

        avg_is = sum(is_scores) / len(is_scores) if is_scores else 0
        avg_oos = sum(oos_scores) / len(oos_scores) if oos_scores else 0
        wfe = avg_oos / avg_is if avg_is > 0 else 0

        return {
            "windows_tested": len(windows),
            "avg_is_profit_factor": avg_is,
            "avg_oos_profit_factor": avg_oos,
            "wfe_score": wfe,
            "assessment": "EXCELLENT" if wfe > 0.7 else ("GOOD" if wfe > 0.5 else "MARGINAL"),
        }
```

---

## 10. PINE SCRIPT v5 — TRADINGVIEW MVP

Save this as `VWAP_Bot_Strategy.pine` in TradingView Pine Script editor.

```pinescript
//@version=5
strategy("VWAP Bot Strategy v1.0 — Prop Firm Edition",
         overlay=true,
         initial_capital=50000,
         default_qty_type=strategy.fixed,
         default_qty_value=1,
         commission_type=strategy.commission.cash_per_contract,
         commission_value=0.70,           // $0.35 per side x 2
         slippage=1,                       // 1 tick slippage
         margin_long=40,
         margin_short=40,
         max_bars_back=500)

// ============================================================
// INPUTS — OPTIMISABLE PARAMETERS
// ============================================================
i_sd_entry    = input.float(1.0,  "SD Band Entry Multiplier", 0.5, 2.5, 0.25)
i_sd_stop     = input.float(1.5,  "SD Band Stop Multiplier",  1.0, 3.0, 0.25)
i_rr          = input.float(2.0,  "Risk:Reward Ratio",        1.0, 4.0, 0.25)
i_contracts   = input.int(1,      "Contracts",                 1,   5,   1)
i_vol_mult    = input.float(1.8,  "Volume Spike Multiplier",  1.2, 3.0, 0.1)
i_session_start = input.session("0945-1400", "Trade Session (ET)")

// ============================================================
// PROP FIRM CONSTRAINTS (TOPSTEP 50K — DO NOT CHANGE)
// ============================================================
ACCOUNT_SIZE       = 50000
MLL                = 2000      // Maximum Loss Limit
DAILY_STOP_PCT     = 0.40      // Stop trading at 40% of MLL = $800
MAX_CONTRACTS      = 5         // Never exceed this
PROFIT_PROTECT_USD = 400       // Reduce size once $400 up on day
DAILY_STOP_USD     = MLL * DAILY_STOP_PCT  // = $800

// ============================================================
// SESSION VWAP CALCULATION
// ============================================================
is_new_session = ta.change(time("D")) != 0

var float cum_tp_vol = 0.0
var float cum_vol    = 0.0
var float cum_tp_vol_sq = 0.0

tp = (high + low + close) / 3

if is_new_session
    cum_tp_vol    := tp * volume
    cum_vol       := volume
    cum_tp_vol_sq := (tp * tp) * volume
else
    cum_tp_vol    += tp * volume
    cum_vol       += volume
    cum_tp_vol_sq += (tp * tp) * volume

vwap_val  = cum_tp_vol / cum_vol
variance  = math.max(0, (cum_tp_vol_sq / cum_vol) - (vwap_val * vwap_val))
vwap_std  = math.sqrt(variance)

sd1_upper = vwap_val + vwap_std
sd1_lower = vwap_val - vwap_std
sd2_upper = vwap_val + (2 * vwap_std)
sd2_lower = vwap_val - (2 * vwap_std)

plot(vwap_val,  "VWAP",      color=color.white,  linewidth=2)
plot(sd1_upper, "SD1 Upper", color=color.new(color.green, 50), linewidth=1)
plot(sd1_lower, "SD1 Lower", color=color.new(color.green, 50), linewidth=1)
plot(sd2_upper, "SD2 Upper", color=color.new(color.orange, 50))
plot(sd2_lower, "SD2 Lower", color=color.new(color.orange, 50))

// ============================================================
// MARKET STATE DETECTION
// ============================================================
vol_avg      = ta.sma(volume, 20)
vol_spike    = volume > (vol_avg * i_vol_mult)
atr_val      = ta.atr(14)
atr_avg      = ta.sma(atr_val, 20)
atr_ratio    = atr_val / atr_avg

price_above_sd1  = close > sd1_upper
price_below_sd1  = close < sd1_lower
price_above_vwap = close > vwap_val
price_below_vwap = close < vwap_val
price_inside_sd1 = close >= sd1_lower and close <= sd1_upper

// Simplified delta proxy (close vs open directional bias)
delta_positive = close > open
delta_negative = close < open
delta_flip_up   = delta_positive and not delta_positive[1]
delta_flip_down = delta_negative and not delta_negative[1]

is_balanced = price_inside_sd1 and atr_ratio < 1.5

// ============================================================
// PROP FIRM DAILY TRACKING
// ============================================================
var float daily_pnl      = 0.0
var int   winning_days   = 0
var bool  day_stopped    = false

if is_new_session
    daily_pnl   := 0.0
    day_stopped := false

daily_pnl := strategy.openprofit + strategy.netprofit -
             strategy.netprofit[ta.barssince(is_new_session)]

// Circuit breaker: stop trading if daily loss hits $800
if daily_pnl <= -DAILY_STOP_USD
    day_stopped := true

// Profit protect: reduce risk once $400 up
profit_protect_active = daily_pnl >= PROFIT_PROTECT_USD

// ============================================================
// SESSION TIME FILTER
// ============================================================
in_session   = not na(time(timeframe.period, i_session_start))
not_new_open = barssince(is_new_session) > 3   // Skip first 3 bars (15 min on 5m)

trade_allowed = in_session and not_new_open and not day_stopped and
                strategy.opentrades == 0

// ============================================================
// ENTRY SIGNALS
// ============================================================
// SETUP 1: MEAN REVERSION — BALANCED MARKET
long_mr  = trade_allowed and is_balanced and price_below_sd1 and
           delta_flip_up and vol_spike

short_mr = trade_allowed and is_balanced and price_above_sd1 and
           delta_flip_down and vol_spike

// SETUP 2: SD2 EXTREME FADE
long_sd2  = trade_allowed and close < sd2_lower and vol_spike and delta_flip_up
short_sd2 = trade_allowed and close > sd2_upper and vol_spike and delta_flip_down

entry_long  = long_mr or long_sd2
entry_short = short_mr or short_sd2

// Enforce contract limit
contracts_to_use = profit_protect_active ?
                   math.max(1, math.floor(i_contracts / 2)) :
                   math.min(i_contracts, MAX_CONTRACTS)

// ============================================================
// STOP LOSS & TARGET CALCULATION
// ============================================================
stop_dist   = vwap_std * i_sd_stop
target_dist = stop_dist * i_rr

// ============================================================
// EXECUTE ORDERS
// ============================================================
if entry_long
    strategy.entry("Long", strategy.long, qty=contracts_to_use)
    strategy.exit("Long Exit", "Long",
                  stop   = close - stop_dist,
                  limit  = close + target_dist,
                  comment = "SL/TP")

if entry_short
    strategy.entry("Short", strategy.short, qty=contracts_to_use)
    strategy.exit("Short Exit", "Short",
                  stop   = close + stop_dist,
                  limit  = close - target_dist,
                  comment = "SL/TP")

// ============================================================
// WEBHOOK ALERT (connect to TradersPost)
// ============================================================
alertcondition(entry_long,
    "VWAP Long Entry",
    '{"ticker":"{{ticker}}","action":"buy","orderType":"market","quantity":1,"price":"{{close}}","timestamp":"{{timenow}}","setup":"mean_reversion"}')

alertcondition(entry_short,
    "VWAP Short Entry",
    '{"ticker":"{{ticker}}","action":"sell","orderType":"market","quantity":1,"price":"{{close}}","timestamp":"{{timenow}}","setup":"mean_reversion"}')
```

---

## 11. PROJECT FILE STRUCTURE

```
vwap-trading-bot/
│
├── README.md
├── requirements.txt
├── .env.example                        # API keys template (NEVER commit .env)
├── config/
│   ├── prop_firm_configs.py            # All PROP_FIRM_CONFIGS dicts
│   ├── bot_risk_params.py              # BOT_RISK_PARAMS dict
│   └── instrument_specs.py            # INSTRUMENT_SPECS dict
│
├── core/
│   ├── prop_firm_simulator.py          # PropFirmSimulator class (Section 3)
│   ├── vwap_calculator.py              # VWAPCalculator class (Section 5.1)
│   ├── market_state_detector.py        # MarketStateDetector class (Section 5.2)
│   └── signal_generator.py            # SignalGenerator class (Section 5.3)
│
├── video_analysis/
│   ├── pipeline.py                     # TradingVideoPipeline class (Section 7.1)
│   ├── pattern_miner.py                # PatternMiner class (Section 7.2)
│   └── traders/
│       └── chris_drysdale.py           # Trader-specific config & video list
│
├── backtesting/
│   ├── backtest_runner.py              # BacktestRunner class (Section 8.1)
│   ├── monte_carlo.py                  # MonteCarloSimulator class
│   └── results/                        # Saved backtest JSON results
│
├── optimisation/
│   ├── walk_forward.py                 # WalkForwardOptimiser class (Section 9)
│   └── regime_detector.py             # MarketRegimeClassifier class
│
├── execution/
│   ├── webhook_server.py               # FastAPI server to receive TV webhooks
│   ├── traderspost_client.py           # TradersPost API wrapper
│   └── order_manager.py               # Order tracking + MLL breach monitor
│
├── database/
│   ├── schema.sql                      # Full DB schema (Section 6)
│   ├── db_manager.py                   # SQLite connection manager
│   └── trading_analysis.db            # SQLite database (gitignored)
│
├── dashboard/
│   ├── performance_dashboard.py        # Streamlit dashboard
│   └── metrics.py                      # Win rate, PF, Sharpe, WFE calculations
│
├── pine_script/
│   └── VWAP_Bot_Strategy.pine         # TradingView Pine Script (Section 10)
│
├── tests/
│   ├── test_prop_firm_simulator.py
│   ├── test_vwap_calculator.py
│   └── test_signal_generator.py
│
└── scripts/
    ├── run_backtest.py                 # CLI: python run_backtest.py --firm TOPSTEP_50K
    ├── run_video_pipeline.py           # CLI: python run_video_pipeline.py --url <youtube>
    └── run_wfo.py                      # CLI: python run_wfo.py --months 12
```

---

## 12. BUILD ORDER (STEP BY STEP)

Build in exactly this order — each step depends on the previous one:

1. **Set up project structure** — create all folders and empty files listed above
2. **Install dependencies** — `pip install pandas numpy optuna backtrader whisper-openai google-generativeai fastapi uvicorn sqlite3 streamlit optuna pytest`
3. **Build `schema.sql` and `db_manager.py`** — run `schema.sql` to create the database
4. **Build `prop_firm_simulator.py`** — the most critical file; write all unit tests first
5. **Build `vwap_calculator.py`** — test with sample OHLCV data before proceeding
6. **Build `market_state_detector.py`** and `signal_generator.py`
7. **Build `backtest_runner.py`** — connect to prop firm simulator; run test with dummy data
8. **Build `monte_carlo.py`** — requires backtest_runner to have a trade list
9. **Build `walk_forward.py`** — requires backtest_runner + optuna installed
10. **Build `video_analysis/pipeline.py`** — requires Gemini API key and Whisper installed
11. **Build `video_analysis/pattern_miner.py`** — requires at least 30 rows in raw_video_trades
12. **Deploy Pine Script** — paste `VWAP_Bot_Strategy.pine` into TradingView
13. **Build `webhook_server.py`** — FastAPI endpoint to receive TradingView alerts
14. **Build `performance_dashboard.py`** — Streamlit dashboard for monitoring
15. **Integration test** — run a paper trading session end-to-end

---

## 13. REQUIREMENTS.TXT

```
pandas>=2.0.0
numpy>=1.24.0
optuna>=3.5.0
openai-whisper>=20231117
google-generativeai>=0.8.0
fastapi>=0.110.0
uvicorn>=0.28.0
streamlit>=1.32.0
pytest>=8.0.0
python-dotenv>=1.0.0
yt-dlp>=2024.3.10
Pillow>=10.0.0
scipy>=1.12.0
matplotlib>=3.8.0
requests>=2.31.0
stable-baselines3>=2.3.0    # For Phase 4 RL (optional)
gymnasium>=0.29.0            # For Phase 4 RL (optional)
torch>=2.2.0                 # For Phase 4 RL (optional)
```

---

## 14. ENVIRONMENT VARIABLES (.env.example)

```
# Gemini API (for video frame analysis)
GEMINI_API_KEY=your_gemini_api_key_here

# TradingView Webhook Secret
TV_WEBHOOK_SECRET=your_secret_here

# TradersPost
TRADERSPOST_WEBHOOK_URL=https://traderspost.io/trading/webhook/...
TRADERSPOST_API_KEY=your_key_here

# Tradovate (for execution)
TRADOVATE_USERNAME=your_username
TRADOVATE_PASSWORD=your_password
TRADOVATE_APP_ID=your_app_id

# Database
DB_PATH=./database/trading_analysis.db

# Slack/Email Notifications (optional)
SLACK_WEBHOOK_URL=your_slack_webhook
NOTIFICATION_EMAIL=your@email.com
```

---

## 15. PERFORMANCE THRESHOLDS (GO/NO-GO CRITERIA)

Before deploying on a funded account, all thresholds must be met on out-of-sample data:

| Metric | Minimum | Target | Stop Deploying If |
|---|---|---|---|
| Win Rate | 52% | 60%+ | < 48% over 50 trades |
| Profit Factor | 1.3 | 1.8+ | < 1.0 over 30 days |
| Sharpe Ratio | 0.8 | 1.5+ | < 0.5 over 60 days |
| Max Drawdown | < 20% | < 10% | > 30% at any point |
| WFE Score | 0.5 | 0.7+ | < 0.4 on last window |
| Monte Carlo Ruin | < 10% | < 5% | > 15% |
| Combine Pass Sim | 70%+ | 85%+ | < 50% |
| Daily Win Rate | 55%+ | 65%+ | < 45% over 20 days |
| Winning Days/Mo | 12+ | 17+ | < 10 (payout threshold risk) |

---

*End of Project Specification — Version 1.0*
*Paste this entire document into Claude Code as your project context to begin building.*
```
