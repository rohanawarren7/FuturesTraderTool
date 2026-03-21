from __future__ import annotations
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.prop_firm_configs import PROP_FIRM_CONFIGS


class PropFirmSimulator:
    """
    Simulates funded account constraints during backtesting.
    Wraps a backtest engine and enforces all prop firm rules in real time.
    Must be called on every bar and every trade execution.

    Bug fixes vs original spec:
      - Bug 1: self.opening_balance_today tracks the day's opening balance correctly.
               close_day() sets this on each new day so daily_pnl is always accurate.
      - Bug 2: self.intraday_daily_pnl is updated on every tick via update_intraday()
               so the Apex consistency rule fires in real time, not just at EOD.
    """

    def __init__(self, config: dict):
        self.config = config
        self.account_size = config["account_size"]
        self.balance = config["account_size"]
        self.equity = config["account_size"]

        # High-water marks
        self.peak_eod_balance = config["account_size"]
        self.peak_intraday_equity = config["account_size"]

        # --- Bug 1 fix: track opening balance per day ---
        self.opening_balance_today: float = config["account_size"]
        self.current_day: Optional[str] = None  # set in close_day()

        # PnL tracking
        self.daily_pnl: float = 0.0           # EOD-settled
        self.intraday_daily_pnl: float = 0.0  # Bug 2 fix: live intraday figure
        self.total_pnl: float = 0.0
        self.lifetime_withdrawals: float = 0.0

        # Logs
        self.trade_log: list = []
        self.daily_log: dict = {}

        # Payout tracking
        self.winning_days_since_last_payout: int = 0
        self.winning_day_pnl_threshold: float = config.get("min_winning_day_pnl", 200)

        # Status
        self.account_blown: bool = False
        self.combine_passed: bool = False
        self.status: str = "COMBINE"   # COMBINE → FUNDED → BLOWN → PASSED
        self.breach_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # CORE RULE CHECKERS
    # ------------------------------------------------------------------

    def get_mll_floor(self) -> float:
        """
        Returns the current Maximum Loss Limit floor (the equity level that,
        if touched, blows the account).

        Topstep (EOD trailing): floor = peak_eod_balance - max_loss_limit
        Apex (INTRADAY trailing): floor = peak_intraday_equity - max_loss_limit
            — but stops trailing once profit >= trailing_stops_at_profit
        FTMO (EOD, balance-based): floor = max(account_size, peak_eod) - max_loss_amount
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
                # Apex: drawdown locks once profit exceeds threshold
                return self.account_size - mll
            return self.peak_intraday_equity - mll

        # EOD (Topstep default)
        return self.peak_eod_balance - mll

    def get_daily_loss_limit_floor(self) -> float:
        """
        Returns the absolute equity floor from the daily loss limit rule.
        Only applies to FTMO ($5,000/day). Topstep has no daily loss limit.
        """
        if self.config.get("daily_loss_limit"):
            return self.opening_balance_today - self.config["daily_loss_limit"]
        return -float("inf")

    def check_breach(self) -> tuple[bool, str]:
        """
        Checks all breach conditions. Returns (is_breached, reason).
        Call this on every tick/bar update via update_intraday().
        """
        mll_floor = self.get_mll_floor()
        daily_floor = self.get_daily_loss_limit_floor()

        if self.equity < mll_floor:
            return True, f"MLL_BREACH: Equity {self.equity:.2f} < MLL Floor {mll_floor:.2f}"

        if self.equity < daily_floor:
            return True, (
                f"DAILY_LOSS_BREACH: Equity {self.equity:.2f} "
                f"< Daily Floor {daily_floor:.2f}"
            )

        # Bug 2 fix: Apex consistency rule uses intraday_daily_pnl (live),
        # not self.daily_pnl (only updated at EOD).
        if self.config.get("consistency_rule") and self.total_pnl > 0:
            if self.intraday_daily_pnl > (self.total_pnl * 0.30):
                return True, (
                    f"CONSISTENCY_BREACH: Intraday daily PnL {self.intraday_daily_pnl:.2f} "
                    f"> 30% of total {self.total_pnl:.2f}"
                )

        return False, ""

    def check_contract_limit(self, requested_contracts: int) -> int:
        """Caps contract size to the prop firm's maximum. Returns allowed contracts."""
        max_c = self.config.get("max_contracts")
        if max_c is None:
            return requested_contracts
        return min(requested_contracts, max_c)

    def check_combine_passed(self) -> bool:
        """Returns True if the profit target has been reached."""
        return (self.balance - self.account_size) >= self.config["profit_target"]

    def check_payout_eligible(self) -> tuple[bool, float]:
        """
        Returns (is_eligible, max_payout_amount).
        Topstep: requires 5 winning days >= $200 net each since last payout.
        """
        min_days = self.config.get("min_payout_days")
        if min_days and self.winning_days_since_last_payout < min_days:
            return False, 0.0

        profit_in_account = self.balance - self.account_size
        if profit_in_account <= 0:
            return False, 0.0

        max_pct = self.config.get("payout_max_pct", 0.50)
        max_fixed = self.config.get("max_payout_per_request", float("inf"))
        payout = min(profit_in_account * max_pct, max_fixed, profit_in_account)
        return True, max(0.0, payout)

    def request_payout(self) -> float:
        """
        Processes a payout. Applies 100% vs configured split depending on
        lifetime withdrawals vs the first-withdrawal bonus threshold.
        Returns net amount received by the trader.
        """
        eligible, gross_amount = self.check_payout_eligible()
        if not eligible or gross_amount <= 0:
            return 0.0

        bonus_cap = self.config.get("first_withdrawal_bonus", 0)
        bonus_remaining = max(0.0, bonus_cap - self.lifetime_withdrawals)

        if gross_amount <= bonus_remaining:
            net = gross_amount  # 100% during bonus window
        else:
            at_100 = bonus_remaining
            at_split = gross_amount - bonus_remaining
            net = at_100 + (at_split * self.config["profit_split"])

        self.balance -= gross_amount
        self.lifetime_withdrawals += gross_amount
        self.winning_days_since_last_payout = 0

        # Topstep: after payout, MLL resets to current (lower) balance
        if self.config["firm"] == "Topstep":
            self.peak_eod_balance = self.balance
            self.opening_balance_today = self.balance

        return net

    # ------------------------------------------------------------------
    # STATE UPDATERS
    # ------------------------------------------------------------------

    def update_intraday(self, current_equity: float):
        """
        Call on every tick/bar with current equity (settled balance + open PnL).
        Updates intraday high-water mark and checks for breach.
        """
        self.equity = current_equity

        # Bug 2 fix: keep intraday_daily_pnl live so consistency rule works
        self.intraday_daily_pnl = current_equity - self.opening_balance_today

        if self.config.get("trailing_type") == "INTRADAY":
            if current_equity > self.peak_intraday_equity:
                self.peak_intraday_equity = current_equity

        if not self.account_blown:
            breached, reason = self.check_breach()
            if breached:
                self.account_blown = True
                self.breach_reason = reason
                self.status = "BLOWN"

    def close_day(self, eod_balance: float, day_label: str = ""):
        """
        Call at end of each trading day (4:00 PM ET).
        Settles the day: updates balance, resets intraday trackers,
        updates EOD high-water mark, and checks combine pass.

        Bug 1 fix: opening_balance_today is set correctly here so that
        the next day's daily_pnl and daily_floor calculations are accurate.
        """
        # Settle daily PnL against today's opening balance (Bug 1 fix)
        self.daily_pnl = eod_balance - self.opening_balance_today
        self.balance = eod_balance

        # Update EOD high-water mark (Topstep, FTMO)
        if eod_balance > self.peak_eod_balance:
            self.peak_eod_balance = eod_balance

        # Reset intraday peak for next session
        self.peak_intraday_equity = eod_balance

        # Bug 1 fix: roll opening balance forward to start of next day
        self.opening_balance_today = eod_balance
        self.intraday_daily_pnl = 0.0

        # Track winning days for payout eligibility
        if self.daily_pnl >= self.winning_day_pnl_threshold:
            self.winning_days_since_last_payout += 1

        self.total_pnl = self.balance - self.account_size
        self.current_day = day_label

        self.daily_log[day_label] = {
            "eod_balance": eod_balance,
            "daily_pnl": self.daily_pnl,
            "mll_floor": self.get_mll_floor(),
            "winning_days_since_payout": self.winning_days_since_last_payout,
        }

        # Check combine passed
        if not self.combine_passed and self.check_combine_passed():
            self.combine_passed = True
            self.status = "FUNDED"

    def get_safe_daily_loss_budget(self) -> float:
        """
        Returns the maximum dollar loss the bot should accept today.
        Set to 40% of MLL as a hard intraday circuit breaker.
        """
        return self.config["max_loss_limit"] * 0.40

    def get_report(self) -> dict:
        """Returns a summary dict for a backtest or live session."""
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
