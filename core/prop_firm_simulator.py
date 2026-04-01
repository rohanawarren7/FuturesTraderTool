from __future__ import annotations

from typing import Optional
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.prop_firm_configs import PROP_FIRM_CONFIGS


class PropFirmSimulator:
    """
    Simulates funded-account constraints during backtesting and live risk review.

    Key improvements:
    - honours Topstep Live Funded daily loss limits
    - exposes dynamic live risk expansion tiers
    - distinguishes internal bot hard stops from firm-level hard stops
    - keeps opening balance and intraday daily PnL accurate across sessions
    """

    def __init__(self, config: dict):
        self.config = config
        self.account_size = config["account_size"]
        self.balance = config["account_size"]
        self.equity = config["account_size"]

        self.peak_eod_balance = config["account_size"]
        self.peak_intraday_equity = config["account_size"]
        self.opening_balance_today: float = config["account_size"]
        self.current_day: Optional[str] = None

        self.daily_pnl: float = 0.0
        self.intraday_daily_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.lifetime_withdrawals: float = 0.0

        self.trade_log: list = []
        self.daily_log: dict = {}
        self.active_days: int = 0

        self.winning_days_since_last_payout: int = 0
        self.winning_day_pnl_threshold: float = config.get("min_winning_day_pnl", 200)

        self.account_blown: bool = False
        self.combine_passed: bool = False
        self.status: str = "COMBINE"
        self.breach_reason: Optional[str] = None

    def get_mll_floor(self) -> float:
        """
        Returns the current maximum loss limit floor.

        Topstep uses an end-of-day trailing drawdown model in this simulator.
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
                return self.account_size - mll
            return self.peak_intraday_equity - mll

        return self.peak_eod_balance - mll

    def get_daily_loss_limit(self) -> Optional[float]:
        """Return the firm-level daily loss limit if one exists."""
        return self._get_active_risk_tier().get("daily_loss_limit", self.config.get("daily_loss_limit"))

    def get_daily_loss_limit_floor(self) -> float:
        """Returns the absolute equity floor implied by the daily loss limit."""
        daily_loss_limit = self.get_daily_loss_limit()
        if daily_loss_limit:
            return self.opening_balance_today - daily_loss_limit
        return -float("inf")

    def _get_active_risk_tier(self) -> dict:
        tiers = sorted(
            self.config.get("live_risk_expansion_tiers", []),
            key=lambda item: item.get("min_net_profit", 0),
        )
        if not tiers:
            return {
                "name": "default",
                "min_net_profit": 0,
                "max_position_size": self.config.get("max_contracts"),
                "daily_loss_limit": self.config.get("daily_loss_limit"),
                "active_days_required": 0,
            }

        eligible = tiers[0]
        for tier in tiers:
            if self.total_pnl >= tier.get("min_net_profit", 0):
                required_days = tier.get(
                    "active_days_required",
                    self.config.get("risk_expansion_active_days_required", 0),
                )
                if self.active_days >= required_days:
                    eligible = tier
        return eligible

    def get_current_contract_limit(self) -> Optional[int]:
        tier = self._get_active_risk_tier()
        return tier.get("max_position_size", self.config.get("max_contracts"))

    def check_breach(self) -> tuple[bool, str]:
        mll_floor = self.get_mll_floor()
        daily_floor = self.get_daily_loss_limit_floor()

        if self.equity < mll_floor:
            return True, f"MLL_BREACH: Equity {self.equity:.2f} < MLL Floor {mll_floor:.2f}"

        if self.equity < daily_floor:
            return True, (
                f"DAILY_LOSS_BREACH: Equity {self.equity:.2f} "
                f"< Daily Floor {daily_floor:.2f}"
            )

        if self.config.get("consistency_rule") and self.total_pnl > 0:
            if self.intraday_daily_pnl > (self.total_pnl * 0.30):
                return True, (
                    f"CONSISTENCY_BREACH: Intraday daily PnL {self.intraday_daily_pnl:.2f} "
                    f"> 30% of total {self.total_pnl:.2f}"
                )

        return False, ""

    def check_contract_limit(self, requested_contracts: int) -> int:
        max_contracts = self.get_current_contract_limit()
        if max_contracts is None:
            return requested_contracts
        return min(requested_contracts, max_contracts)

    def check_combine_passed(self) -> bool:
        return (self.balance - self.account_size) >= self.config["profit_target"]

    def check_payout_eligible(self) -> tuple[bool, float]:
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
        eligible, gross_amount = self.check_payout_eligible()
        if not eligible or gross_amount <= 0:
            return 0.0

        bonus_cap = self.config.get("first_withdrawal_bonus", 0)
        bonus_remaining = max(0.0, bonus_cap - self.lifetime_withdrawals)

        if gross_amount <= bonus_remaining:
            net = gross_amount
        else:
            at_100 = bonus_remaining
            at_split = gross_amount - bonus_remaining
            net = at_100 + (at_split * self.config["profit_split"])

        self.balance -= gross_amount
        self.lifetime_withdrawals += gross_amount
        self.winning_days_since_last_payout = 0

        if self.config["firm"] == "Topstep":
            self.peak_eod_balance = self.balance
            self.opening_balance_today = self.balance

        return net

    def update_intraday(self, current_equity: float):
        self.equity = current_equity
        self.intraday_daily_pnl = current_equity - self.opening_balance_today

        if self.config.get("trailing_type") == "INTRADAY" and current_equity > self.peak_intraday_equity:
            self.peak_intraday_equity = current_equity

        if not self.account_blown:
            breached, reason = self.check_breach()
            if breached:
                self.account_blown = True
                self.breach_reason = reason
                self.status = "BLOWN"

    def close_day(self, eod_balance: float, day_label: str = "", traded_today: bool = True):
        self.daily_pnl = eod_balance - self.opening_balance_today
        self.balance = eod_balance

        if traded_today:
            self.active_days += 1

        if eod_balance > self.peak_eod_balance:
            self.peak_eod_balance = eod_balance

        self.peak_intraday_equity = eod_balance
        self.opening_balance_today = eod_balance
        self.intraday_daily_pnl = 0.0

        if self.daily_pnl >= self.winning_day_pnl_threshold:
            self.winning_days_since_last_payout += 1

        self.total_pnl = self.balance - self.account_size
        self.current_day = day_label

        self.daily_log[day_label] = {
            "eod_balance": eod_balance,
            "daily_pnl": self.daily_pnl,
            "mll_floor": self.get_mll_floor(),
            "daily_loss_limit": self.get_daily_loss_limit(),
            "active_risk_tier": self._get_active_risk_tier(),
            "winning_days_since_payout": self.winning_days_since_last_payout,
            "active_days": self.active_days,
        }

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
        active_tier = self._get_active_risk_tier()
        return {
            "firm": self.config["firm"],
            "program_stage": self.config.get("program_stage"),
            "account_size": self.account_size,
            "final_balance": self.balance,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit": self.get_daily_loss_limit(),
            "daily_loss_limit_floor": self.get_daily_loss_limit_floor(),
            "lifetime_withdrawals": self.lifetime_withdrawals,
            "combine_passed": self.combine_passed,
            "account_blown": self.account_blown,
            "breach_reason": self.breach_reason,
            "status": self.status,
            "peak_eod_balance": self.peak_eod_balance,
            "current_mll_floor": self.get_mll_floor(),
            "winning_days_since_payout": self.winning_days_since_last_payout,
            "active_days": self.active_days,
            "active_risk_tier": active_tier,
            "current_contract_limit": self.get_current_contract_limit(),
        }


__all__ = ["PropFirmSimulator", "PROP_FIRM_CONFIGS"]
