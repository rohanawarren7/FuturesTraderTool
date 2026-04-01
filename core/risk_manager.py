"""
Centralized Risk Management System for VWAP Trading Bot.
All risk checks must pass before any trade is executed.
"""

from __future__ import annotations

from typing import Optional, Dict, Any
from datetime import datetime
import pandas as pd


class RiskManager:
    """
    Manages all trading risk constraints.
    Must be called before every trade to verify risk limits.
    """

    def __init__(self, prop_firm_config: dict, bot_risk_params: Optional[dict] = None):
        self.prop_config = prop_firm_config
        self.bot_params = bot_risk_params or self._default_bot_params()

        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_trade_pnl = 0.0
        self.trades_today: list[dict] = []

        self.current_date: Optional[datetime.date] = None
        self.max_daily_loss_hit = False

    def _default_bot_params(self) -> dict:
        return {
            "max_daily_trades": 5,
            "max_concurrent_positions": 1,
            "max_consecutive_losses": 3,
            "max_risk_per_trade_pct": 0.01,
            "volatility_lookback": 20,
            "position_size_formula": "kelly_half",
            "daily_loss_limit_pct": 0.40,
            "internal_daily_hard_stop_usd": 350,
            "mll_proximity_pct": 0.10,
            "breathing_room_multiplier": 1.5,
            "correlation_threshold": 0.7,
            "min_time_between_trades_minutes": 5,
        }

    def reset_daily_counters(self, date: datetime.date):
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.trades_today = []
        self.max_daily_loss_hit = False
        self.current_date = date

    def can_trade(
        self,
        market_state: dict,
        account_state: dict,
        proposed_trade: Optional[dict] = None,
    ) -> tuple[bool, str]:
        checks = [
            ("daily_trade_limit", self._check_daily_trade_limit),
            ("consecutive_losses", self._check_consecutive_losses),
            ("daily_loss_limit", self._check_daily_loss_limit),
            ("mll_proximity", self._check_mll_proximity),
            ("time_between_trades", self._check_time_between_trades),
            ("correlation", self._check_correlation),
            ("trading_hours", self._check_trading_hours),
            ("instrument_allow_list", self._check_instrument_allow_list),
        ]

        for check_name, check_func in checks:
            allowed, reason = check_func(market_state, account_state, proposed_trade)
            if not allowed:
                return False, f"{check_name.upper()}: {reason}"

        return True, ""

    def _check_daily_trade_limit(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        if self.daily_trade_count >= self.bot_params["max_daily_trades"]:
            return False, f"Max daily trades ({self.bot_params['max_daily_trades']}) reached"
        return True, ""

    def _check_consecutive_losses(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        if self.consecutive_losses >= self.bot_params["max_consecutive_losses"]:
            return False, f"{self.consecutive_losses} consecutive losses, trading halted"
        return True, ""

    def _effective_daily_loss_limit(self) -> float:
        internal_limit = self.bot_params.get("internal_daily_hard_stop_usd")
        firm_limit = self.prop_config.get("daily_loss_limit")
        fallback_limit = self.prop_config.get("max_loss_limit", 2000) * self.bot_params["daily_loss_limit_pct"]

        if internal_limit and firm_limit:
            return min(internal_limit, firm_limit)
        if internal_limit:
            return internal_limit
        if firm_limit:
            return firm_limit
        return fallback_limit

    def _check_daily_loss_limit(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        if self.max_daily_loss_hit:
            return False, "Daily loss limit already hit"

        daily_limit = self._effective_daily_loss_limit()
        if self.daily_pnl <= -daily_limit:
            self.max_daily_loss_hit = True
            return False, f"Daily loss ${abs(self.daily_pnl):.0f} >= limit ${daily_limit:.0f}"

        if proposed_trade:
            max_loss = proposed_trade.get("max_loss", 0)
            if (self.daily_pnl - max_loss) <= -daily_limit:
                return False, "Trade would exceed daily loss limit"

        return True, ""

    def _check_mll_proximity(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        current_equity = account_state.get("equity", 0)
        mll_floor = account_state.get("mll_floor", 0)

        if mll_floor <= 0:
            return True, ""

        distance_to_floor = current_equity - mll_floor
        mll = self.prop_config.get("max_loss_limit", 2000)
        proximity_threshold = mll * self.bot_params["mll_proximity_pct"]

        if distance_to_floor <= proximity_threshold:
            return False, f"Within ${distance_to_floor:.0f} of MLL floor (${proximity_threshold:.0f} threshold)"

        return True, ""

    def _check_time_between_trades(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        if not self.trades_today:
            return True, ""

        min_minutes = self.bot_params["min_time_between_trades_minutes"]
        last_trade_time = self.trades_today[-1].get("entry_time")

        if last_trade_time:
            if isinstance(last_trade_time, str):
                last_trade_time = pd.to_datetime(last_trade_time)
            current_time = pd.to_datetime(market_state.get("timestamp", datetime.now()))
            minutes_diff = (current_time - last_trade_time).total_seconds() / 60

            if minutes_diff < min_minutes:
                return False, f"Only {minutes_diff:.1f} min since last trade (need {min_minutes})"

        return True, ""

    def _check_correlation(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        current_positions = account_state.get("positions", [])
        if not current_positions or not proposed_trade:
            return True, ""

        proposed_instrument = proposed_trade.get("instrument", "")
        correlation_groups = {
            "indices": ["MES", "ES", "MNQ", "NQ"],
            "commodities": ["CL", "GC", "SI"],
        }

        for group_name, instruments in correlation_groups.items():
            if proposed_instrument in instruments:
                for pos in current_positions:
                    if pos.get("instrument") in instruments:
                        return False, f"Already have position in correlated {group_name}"

        return True, ""

    def _check_trading_hours(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        timestamp = market_state.get("timestamp", datetime.now())
        if isinstance(timestamp, str):
            timestamp = pd.to_datetime(timestamp)

        et_hour = (timestamp.hour - 5) % 24
        et_minute = timestamp.minute
        minutes_since_open = (et_hour - 9) * 60 + (et_minute - 30)

        if minutes_since_open < 15:
            return False, "Within first 15 minutes of RTH"
        if minutes_since_open > 375:
            return False, "Within last 15 minutes of RTH"

        return True, ""

    def _check_instrument_allow_list(self, market_state: dict, account_state: dict, proposed_trade: Optional[dict]) -> tuple[bool, str]:
        if not proposed_trade:
            return True, ""
        instrument = proposed_trade.get("instrument")
        allowed = set(self.prop_config.get("instruments", []))
        if allowed and instrument and instrument not in allowed:
            return False, f"Instrument {instrument} not permitted for configured prop-firm profile"
        return True, ""

    def update_after_trade(self, trade_result: dict):
        pnl = trade_result.get("net_pnl", 0)
        self.daily_pnl += pnl
        self.daily_trade_count += 1
        self.trades_today.append(trade_result)

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self.last_trade_pnl = pnl

    def calculate_position_size(
        self,
        account_equity: float,
        atr: float,
        stop_distance: float,
        point_value: float,
        confidence: float = 0.5,
    ) -> int:
        max_risk_dollars = account_equity * self.bot_params["max_risk_per_trade_pct"]

        volatility_factor = 1.0
        if atr > 0:
            vol_ratio = atr / 7.5
            volatility_factor = 1 / max(0.5, vol_ratio)

        kelly_fraction = confidence * 0.5
        risk_per_contract = stop_distance * point_value
        if risk_per_contract <= 0:
            return 1

        base_contracts = int(max_risk_dollars / risk_per_contract)
        adjusted_contracts = int(base_contracts * volatility_factor * kelly_fraction)

        max_contracts = self.prop_config.get("max_contracts", 5)
        contracts = max(1, min(adjusted_contracts, max_contracts))
        return contracts

    def get_risk_summary(self) -> dict:
        return {
            "daily_trades": self.daily_trade_count,
            "max_daily_trades": self.bot_params["max_daily_trades"],
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "max_daily_loss_hit": self.max_daily_loss_hit,
            "effective_daily_loss_limit": self._effective_daily_loss_limit(),
            "trades_remaining": max(0, self.bot_params["max_daily_trades"] - self.daily_trade_count),
        }
