"""
Circuit Breakers System for VWAP Trading Bot.
Hard stops to prevent catastrophic losses and system failures.
"""

from __future__ import annotations

from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta
from enum import Enum
import time


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def _parse_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    return None


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        check_func: Callable,
        reset_timeout_seconds: int = 300,
        auto_reset: bool = True,
        severity: str = "block_entries",
    ):
        self.name = name
        self.check_func = check_func
        self.reset_timeout = reset_timeout_seconds
        self.auto_reset = auto_reset
        self.severity = severity

        self.state = CircuitBreakerState.CLOSED
        self.last_triggered: Optional[datetime] = None
        self.trigger_count = 0
        self.trigger_history: list[dict] = []

    def check(self, context: dict) -> tuple[bool, str]:
        if self.state == CircuitBreakerState.OPEN and self.auto_reset:
            if self._should_attempt_reset():
                self.state = CircuitBreakerState.HALF_OPEN
                print(f"[CircuitBreaker:{self.name}] Attempting auto-reset")

        if self.state == CircuitBreakerState.OPEN:
            return True, f"Circuit breaker {self.name} is OPEN"

        triggered, reason = self.check_func(context)
        if triggered:
            self._trigger(reason)
            return True, reason

        if self.state == CircuitBreakerState.HALF_OPEN:
            self._close()

        return False, ""

    def _trigger(self, reason: str):
        self.state = CircuitBreakerState.OPEN
        self.last_triggered = datetime.utcnow()
        self.trigger_count += 1

        self.trigger_history.append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason,
                "severity": self.severity,
            }
        )
        print(f"[CircuitBreaker:{self.name}] TRIGGERED: {reason}")

    def _close(self):
        self.state = CircuitBreakerState.CLOSED
        print(f"[CircuitBreaker:{self.name}] CLOSED (recovered)")

    def _should_attempt_reset(self) -> bool:
        if self.last_triggered is None:
            return False
        elapsed = (datetime.utcnow() - self.last_triggered).total_seconds()
        return elapsed >= self.reset_timeout

    def manual_reset(self):
        self.state = CircuitBreakerState.CLOSED
        self.last_triggered = None
        print(f"[CircuitBreaker:{self.name}] Manually reset")

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "severity": self.severity,
            "trigger_count": self.trigger_count,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "auto_reset": self.auto_reset,
            "reset_timeout": self.reset_timeout,
        }


class CircuitBreakers:
    """
    Centralized circuit breakers management.
    All critical safety checks must pass through here.
    """

    def __init__(self, prop_firm_config: dict):
        self.prop_config = prop_firm_config
        self.breakers: dict[str, CircuitBreaker] = {}
        self._initialize_breakers()

    def _initialize_breakers(self):
        self.breakers["daily_loss"] = CircuitBreaker(
            name="daily_loss",
            check_func=self._check_daily_loss,
            reset_timeout_seconds=300,
            auto_reset=False,
            severity="flatten_and_halt",
        )
        self.breakers["mll_proximity"] = CircuitBreaker(
            name="mll_proximity",
            check_func=self._check_mll_proximity,
            reset_timeout_seconds=60,
            auto_reset=True,
            severity="block_entries",
        )
        self.breakers["consecutive_losses"] = CircuitBreaker(
            name="consecutive_losses",
            check_func=self._check_consecutive_losses,
            reset_timeout_seconds=3600,
            auto_reset=False,
            severity="block_entries",
        )
        self.breakers["data_freshness"] = CircuitBreaker(
            name="data_freshness",
            check_func=self._check_data_freshness,
            reset_timeout_seconds=30,
            auto_reset=True,
            severity="block_entries",
        )
        self.breakers["broker_connectivity"] = CircuitBreaker(
            name="broker_connectivity",
            check_func=self._check_broker_connectivity,
            reset_timeout_seconds=60,
            auto_reset=True,
            severity="flatten_and_halt",
        )
        self.breakers["order_rate"] = CircuitBreaker(
            name="order_rate",
            check_func=self._check_order_rate,
            reset_timeout_seconds=60,
            auto_reset=True,
            severity="block_entries",
        )
        self.breakers["adverse_skew"] = CircuitBreaker(
            name="adverse_skew",
            check_func=self._check_adverse_skew,
            reset_timeout_seconds=300,
            auto_reset=True,
            severity="block_entries",
        )
        self.breakers["technical_failure"] = CircuitBreaker(
            name="technical_failure",
            check_func=self._check_technical_failure,
            reset_timeout_seconds=30,
            auto_reset=True,
            severity="flatten_and_halt",
        )

    def check_all(self, context: dict) -> tuple[bool, str]:
        for name, breaker in self.breakers.items():
            blocked, reason = breaker.check(context)
            if blocked:
                return False, f"CircuitBreaker.{name}: {reason}"
        return True, ""

    def should_flatten(self) -> bool:
        for breaker in self.breakers.values():
            if breaker.state == CircuitBreakerState.OPEN and breaker.severity == "flatten_and_halt":
                return True
        return False

    def _check_daily_loss(self, context: dict) -> tuple[bool, str]:
        daily_pnl = context.get("daily_pnl", 0)
        firm_limit = self.prop_config.get("daily_loss_limit")
        internal_limit = context.get("internal_daily_hard_stop")

        candidate_limits = [abs(limit) for limit in [firm_limit, internal_limit] if limit]
        if not candidate_limits:
            mll = self.prop_config.get("max_loss_limit", 2000)
            candidate_limits.append(mll * 0.40)

        limit = min(candidate_limits)
        if daily_pnl <= -limit:
            return True, f"Daily loss ${abs(daily_pnl):.0f} >= limit ${limit:.0f}"
        return False, ""

    def _check_mll_proximity(self, context: dict) -> tuple[bool, str]:
        equity = context.get("equity", 0)
        mll_floor = context.get("mll_floor", 0)
        if mll_floor is None or mll_floor <= 0:
            return False, ""

        distance = equity - mll_floor
        mll = self.prop_config.get("max_loss_limit", 2000)
        threshold = context.get("mll_proximity_threshold") or mll * 0.10
        if distance <= threshold:
            return True, f"Within ${distance:.0f} of MLL floor (threshold: ${threshold:.0f})"
        return False, ""

    def _check_consecutive_losses(self, context: dict) -> tuple[bool, str]:
        consecutive_losses = context.get("consecutive_losses", 0)
        if consecutive_losses >= 3:
            return True, f"{consecutive_losses} consecutive losses"
        return False, ""

    def _check_data_freshness(self, context: dict) -> tuple[bool, str]:
        last_data_time = _parse_datetime(context.get("last_data_timestamp"))
        if last_data_time is None:
            return True, "No data timestamp available"

        elapsed = (datetime.utcnow().astimezone(last_data_time.tzinfo) - last_data_time).total_seconds() if last_data_time.tzinfo else (datetime.utcnow() - last_data_time).total_seconds()
        max_age = context.get("max_data_age_seconds", 60)
        if elapsed > max_age:
            return True, f"Data stale: {elapsed:.0f}s old (max: {max_age}s)"
        return False, ""

    def _check_broker_connectivity(self, context: dict) -> tuple[bool, str]:
        last_ping = _parse_datetime(context.get("last_broker_ping"))
        if last_ping is None:
            return True, "No broker ping recorded"

        elapsed = (datetime.utcnow().astimezone(last_ping.tzinfo) - last_ping).total_seconds() if last_ping.tzinfo else (datetime.utcnow() - last_ping).total_seconds()
        timeout = context.get("max_broker_ping_age_seconds", 15)
        if elapsed > timeout:
            return True, f"Broker unresponsive: {elapsed:.0f}s since last ping (timeout: {timeout}s)"
        return False, ""

    def _check_order_rate(self, context: dict) -> tuple[bool, str]:
        recent_orders = context.get("recent_orders", [])
        if len(recent_orders) < 5:
            return False, ""

        cutoff = datetime.utcnow() - timedelta(seconds=60)
        normalised = []
        for item in recent_orders:
            parsed = _parse_datetime(item)
            if parsed is None and isinstance(item, dict):
                parsed = _parse_datetime(item.get("timestamp"))
            if parsed is not None:
                normalised.append(parsed)

        recent_count = sum(1 for order_time in normalised if order_time >= cutoff)
        if recent_count > 10:
            return True, f"Order rate exceeded: {recent_count} orders in 60s (max: 10)"
        return False, ""

    def _check_adverse_skew(self, context: dict) -> tuple[bool, str]:
        recent_fills = context.get("recent_fills", [])
        if len(recent_fills) < 5:
            return False, ""

        slippages = [fill.get("slippage_ticks", 0) for fill in recent_fills[-5:] if isinstance(fill, dict)]
        if not slippages:
            return False, ""
        avg_slippage = sum(slippages) / len(slippages)
        max_avg_slippage = context.get("max_avg_slippage_ticks", 2)
        if avg_slippage > max_avg_slippage:
            return True, f"Adverse skew detected: {avg_slippage:.1f} tick avg slippage"
        return False, ""

    def _check_technical_failure(self, context: dict) -> tuple[bool, str]:
        recent_errors = context.get("recent_errors", [])
        if len(recent_errors) < 3:
            return False, ""

        cutoff = datetime.utcnow() - timedelta(minutes=5)
        recent_error_count = 0
        for error in recent_errors:
            parsed = _parse_datetime(error.get("timestamp") if isinstance(error, dict) else error)
            if parsed and parsed >= cutoff:
                recent_error_count += 1

        if recent_error_count >= 3:
            return True, f"Technical failures: {recent_error_count} errors in 5 minutes"
        return False, ""

    def export_state(self) -> dict:
        return {
            name: {
                "state": breaker.state.value,
                "last_triggered": breaker.last_triggered.isoformat() if breaker.last_triggered else None,
                "trigger_count": breaker.trigger_count,
                "trigger_history": breaker.trigger_history[-20:],
            }
            for name, breaker in self.breakers.items()
        }

    def import_state(self, state: Optional[dict]):
        if not state:
            return
        reverse_states = {item.value: item for item in CircuitBreakerState}
        for name, payload in state.items():
            breaker = self.breakers.get(name)
            if not breaker or not isinstance(payload, dict):
                continue
            breaker.state = reverse_states.get(payload.get("state"), CircuitBreakerState.CLOSED)
            breaker.last_triggered = _parse_datetime(payload.get("last_triggered"))
            breaker.trigger_count = payload.get("trigger_count", 0)
            breaker.trigger_history = payload.get("trigger_history", [])

    def get_status(self) -> dict:
        return {name: breaker.get_status() for name, breaker in self.breakers.items()}

    def manual_reset(self, breaker_name: Optional[str] = None):
        if breaker_name:
            if breaker_name in self.breakers:
                self.breakers[breaker_name].manual_reset()
            else:
                print(f"[CircuitBreakers] ERROR: Unknown breaker {breaker_name}")
        else:
            for breaker in self.breakers.values():
                breaker.manual_reset()
            print("[CircuitBreakers] All breakers manually reset")

    def get_open_breakers(self) -> list[str]:
        return [name for name, breaker in self.breakers.items() if breaker.state == CircuitBreakerState.OPEN]


class CircuitBreakerMonitor:
    def __init__(self, circuit_breakers: CircuitBreakers, check_interval_seconds: int = 5):
        self.circuit_breakers = circuit_breakers
        self.check_interval = check_interval_seconds
        self._running = False
        self.alert_callbacks: list[Callable] = []

    def add_alert_callback(self, callback: Callable):
        self.alert_callbacks.append(callback)

    def start_monitoring(self, context_provider: Callable):
        self._running = True
        print("[CircuitBreakerMonitor] Started monitoring")
        last_open_breakers = set()

        while self._running:
            try:
                context = context_provider()
                self.circuit_breakers.check_all(context)
                current_open = set(self.circuit_breakers.get_open_breakers())

                newly_opened = current_open - last_open_breakers
                for breaker_name in newly_opened:
                    self._alert_breaker_opened(breaker_name)

                newly_closed = last_open_breakers - current_open
                for breaker_name in newly_closed:
                    self._alert_breaker_closed(breaker_name)

                last_open_breakers = current_open
                time.sleep(self.check_interval)
            except Exception as exc:
                print(f"[CircuitBreakerMonitor] ERROR: {exc}")
                time.sleep(self.check_interval)

    def _alert_breaker_opened(self, breaker_name: str):
        message = f"CIRCUIT BREAKER OPENED: {breaker_name}"
        print(f"[CircuitBreakerMonitor] ALERT: {message}")
        for callback in self.alert_callbacks:
            try:
                callback("OPENED", breaker_name, message)
            except Exception as exc:
                print(f"[CircuitBreakerMonitor] Alert callback error: {exc}")

    def _alert_breaker_closed(self, breaker_name: str):
        message = f"CIRCUIT BREAKER CLOSED: {breaker_name}"
        print(f"[CircuitBreakerMonitor] ALERT: {message}")
        for callback in self.alert_callbacks:
            try:
                callback("CLOSED", breaker_name, message)
            except Exception as exc:
                print(f"[CircuitBreakerMonitor] Alert callback error: {exc}")

    def stop_monitoring(self):
        self._running = False
        print("[CircuitBreakerMonitor] Stopped monitoring")
