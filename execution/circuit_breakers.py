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
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Trading halted
    HALF_OPEN = "half_open"  # Testing if conditions recovered


class CircuitBreaker:
    """
    Individual circuit breaker for a specific risk condition.
    """
    
    def __init__(self,
                 name: str,
                 check_func: Callable,
                 reset_timeout_seconds: int = 300,
                 auto_reset: bool = True):
        """
        Initialize circuit breaker.
        
        Args:
            name: Name of the circuit breaker
            check_func: Function that returns (triggered: bool, reason: str)
            reset_timeout_seconds: Seconds before attempting auto-reset
            auto_reset: Whether to auto-reset after timeout
        """
        self.name = name
        self.check_func = check_func
        self.reset_timeout = reset_timeout_seconds
        self.auto_reset = auto_reset
        
        self.state = CircuitBreakerState.CLOSED
        self.last_triggered: Optional[datetime] = None
        self.trigger_count = 0
        self.trigger_history: list[dict] = []
        
    def check(self, context: dict) -> tuple[bool, str]:
        """
        Check if circuit breaker should trigger.
        
        Returns:
            (blocked: bool, reason: str)
        """
        # Check if we should attempt auto-reset
        if self.state == CircuitBreakerState.OPEN and self.auto_reset:
            if self._should_attempt_reset():
                self.state = CircuitBreakerState.HALF_OPEN
                print(f"[CircuitBreaker:{self.name}] Attempting auto-reset")
        
        # If already open, block immediately
        if self.state == CircuitBreakerState.OPEN:
            return True, f"Circuit breaker {self.name} is OPEN"
        
        # Run the check
        triggered, reason = self.check_func(context)
        
        if triggered:
            self._trigger(reason)
            return True, reason
        
        # If half-open and check passed, close the circuit
        if self.state == CircuitBreakerState.HALF_OPEN:
            self._close()
        
        return False, ""
    
    def _trigger(self, reason: str):
        """Trigger the circuit breaker."""
        self.state = CircuitBreakerState.OPEN
        self.last_triggered = datetime.now()
        self.trigger_count += 1
        
        self.trigger_history.append({
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        })
        
        print(f"[CircuitBreaker:{self.name}] TRIGGERED: {reason}")
    
    def _close(self):
        """Close the circuit breaker."""
        self.state = CircuitBreakerState.CLOSED
        print(f"[CircuitBreaker:{self.name}] CLOSED (recovered)")
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_triggered is None:
            return False
        
        elapsed = (datetime.now() - self.last_triggered).total_seconds()
        return elapsed >= self.reset_timeout
    
    def manual_reset(self):
        """Manually reset the circuit breaker."""
        self.state = CircuitBreakerState.CLOSED
        self.last_triggered = None
        print(f"[CircuitBreaker:{self.name}] Manually reset")
    
    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            "name": self.name,
            "state": self.state.value,
            "trigger_count": self.trigger_count,
            "last_triggered": self.last_triggered.isoformat() if self.last_triggered else None,
            "auto_reset": self.auto_reset,
            "reset_timeout": self.reset_timeout
        }


class CircuitBreakers:
    """
    Centralized circuit breakers management.
    All critical safety checks must pass through here.
    """
    
    def __init__(self, prop_firm_config: dict):
        """
        Initialize all circuit breakers.
        
        Args:
            prop_firm_config: Configuration from PROP_FIRM_CONFIGS
        """
        self.prop_config = prop_firm_config
        self.breakers: dict[str, CircuitBreaker] = {}
        
        # Initialize all circuit breakers
        self._initialize_breakers()
        
    def _initialize_breakers(self):
        """Initialize all circuit breaker instances."""
        
        # 1. Daily Loss Limit Breaker
        self.breakers["daily_loss"] = CircuitBreaker(
            name="daily_loss",
            check_func=self._check_daily_loss,
            reset_timeout_seconds=300,  # 5 minutes
            auto_reset=False  # Require manual reset for daily loss
        )
        
        # 2. MLL Proximity Breaker
        self.breakers["mll_proximity"] = CircuitBreaker(
            name="mll_proximity",
            check_func=self._check_mll_proximity,
            reset_timeout_seconds=60,  # 1 minute
            auto_reset=True
        )
        
        # 3. Consecutive Losses Breaker
        self.breakers["consecutive_losses"] = CircuitBreaker(
            name="consecutive_losses",
            check_func=self._check_consecutive_losses,
            reset_timeout_seconds=3600,  # 1 hour
            auto_reset=False  # Wait for new day
        )
        
        # 4. Data Freshness Breaker
        self.breakers["data_freshness"] = CircuitBreaker(
            name="data_freshness",
            check_func=self._check_data_freshness,
            reset_timeout_seconds=30,  # 30 seconds
            auto_reset=True
        )
        
        # 5. Broker Connectivity Breaker
        self.breakers["broker_connectivity"] = CircuitBreaker(
            name="broker_connectivity",
            check_func=self._check_broker_connectivity,
            reset_timeout_seconds=60,  # 1 minute
            auto_reset=True
        )
        
        # 6. Order Rate Limit Breaker
        self.breakers["order_rate"] = CircuitBreaker(
            name="order_rate",
            check_func=self._check_order_rate,
            reset_timeout_seconds=60,
            auto_reset=True
        )
        
        # 7. Adverse Skew Breaker (fill quality degradation)
        self.breakers["adverse_skew"] = CircuitBreaker(
            name="adverse_skew",
            check_func=self._check_adverse_skew,
            reset_timeout_seconds=300,
            auto_reset=True
        )
        
        # 8. Technical Failure Breaker
        self.breakers["technical_failure"] = CircuitBreaker(
            name="technical_failure",
            check_func=self._check_technical_failure,
            reset_timeout_seconds=30,
            auto_reset=True
        )
    
    def check_all(self, context: dict) -> tuple[bool, str]:
        """
        Check all circuit breakers.
        
        Returns:
            (trading_allowed: bool, reason: str)
        """
        for name, breaker in self.breakers.items():
            blocked, reason = breaker.check(context)
            if blocked:
                return False, f"CircuitBreaker.{name}: {reason}"
        
        return True, ""
    
    def _check_daily_loss(self, context: dict) -> tuple[bool, str]:
        """Check if daily loss limit reached."""
        daily_pnl = context.get("daily_pnl", 0)
        mll = self.prop_config.get("max_loss_limit", 2000)
        limit = mll * 0.40  # 40% of MLL
        
        if daily_pnl <= -limit:
            return True, f"Daily loss ${abs(daily_pnl):.0f} >= limit ${limit:.0f}"
        
        return False, ""
    
    def _check_mll_proximity(self, context: dict) -> tuple[bool, str]:
        """Check if too close to MLL floor."""
        equity = context.get("equity", 0)
        mll_floor = context.get("mll_floor", 0)
        
        if mll_floor <= 0:
            return False, ""
        
        distance = equity - mll_floor
        mll = self.prop_config.get("max_loss_limit", 2000)
        threshold = mll * 0.10  # 10% of MLL
        
        if distance <= threshold:
            return True, f"Within ${distance:.0f} of MLL floor (threshold: ${threshold:.0f})"
        
        return False, ""
    
    def _check_consecutive_losses(self, context: dict) -> tuple[bool, str]:
        """Check if too many consecutive losses."""
        consecutive_losses = context.get("consecutive_losses", 0)
        
        if consecutive_losses >= 3:
            return True, f"{consecutive_losses} consecutive losses"
        
        return False, ""
    
    def _check_data_freshness(self, context: dict) -> tuple[bool, str]:
        """Check if market data is fresh."""
        last_data_time = context.get("last_data_timestamp")
        
        if last_data_time is None:
            return True, "No data timestamp available"
        
        if isinstance(last_data_time, str):
            last_data_time = datetime.fromisoformat(last_data_time)
        
        elapsed = (datetime.now() - last_data_time).total_seconds()
        max_age = 60  # 60 seconds
        
        if elapsed > max_age:
            return True, f"Data stale: {elapsed:.0f}s old (max: {max_age}s)"
        
        return False, ""
    
    def _check_broker_connectivity(self, context: dict) -> tuple[bool, str]:
        """Check if broker API is responsive."""
        last_ping = context.get("last_broker_ping")
        
        if last_ping is None:
            return True, "No broker ping recorded"
        
        if isinstance(last_ping, str):
            last_ping = datetime.fromisoformat(last_ping)
        
        elapsed = (datetime.now() - last_ping).total_seconds()
        timeout = 10  # 10 seconds
        
        if elapsed > timeout:
            return True, f"Broker unresponsive: {elapsed:.0f}s since last ping (timeout: {timeout}s)"
        
        return False, ""
    
    def _check_order_rate(self, context: dict) -> tuple[bool, str]:
        """Check if we're submitting orders too quickly."""
        recent_orders = context.get("recent_orders", [])
        
        if len(recent_orders) < 5:
            return False, ""
        
        # Check orders in last 60 seconds
        cutoff = datetime.now() - timedelta(seconds=60)
        recent_count = sum(1 for order_time in recent_orders if order_time > cutoff)
        
        if recent_count > 10:  # More than 10 orders in 60 seconds
            return True, f"Order rate exceeded: {recent_count} orders in 60s (max: 10)"
        
        return False, ""
    
    def _check_adverse_skew(self, context: dict) -> tuple[bool, str]:
        """Check if fill quality has degraded (adverse selection)."""
        recent_fills = context.get("recent_fills", [])
        
        if len(recent_fills) < 5:
            return False, ""
        
        # Calculate average slippage
        slippages = [fill.get("slippage_ticks", 0) for fill in recent_fills[-5:]]
        avg_slippage = sum(slippages) / len(slippages)
        
        if avg_slippage > 2:  # More than 2 ticks average slippage
            return True, f"Adverse skew detected: {avg_slippage:.1f} tick avg slippage"
        
        return False, ""
    
    def _check_technical_failure(self, context: dict) -> tuple[bool, str]:
        """Check for technical failures (exceptions, crashes)."""
        recent_errors = context.get("recent_errors", [])
        
        if len(recent_errors) < 3:
            return False, ""
        
        # Check errors in last 5 minutes
        cutoff = datetime.now() - timedelta(minutes=5)
        recent_error_count = sum(
            1 for error in recent_errors 
            if datetime.fromisoformat(error.get("timestamp", "2000-01-01")) > cutoff
        )
        
        if recent_error_count >= 3:
            return True, f"Technical failures: {recent_error_count} errors in 5 minutes"
        
        return False, ""
    
    def get_status(self) -> dict:
        """Get status of all circuit breakers."""
        return {
            name: breaker.get_status()
            for name, breaker in self.breakers.items()
        }
    
    def manual_reset(self, breaker_name: Optional[str] = None):
        """
        Manually reset circuit breaker(s).
        
        Args:
            breaker_name: Specific breaker to reset, or None for all
        """
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
        """Get list of currently open circuit breakers."""
        return [
            name for name, breaker in self.breakers.items()
            if breaker.state == CircuitBreakerState.OPEN
        ]


class CircuitBreakerMonitor:
    """
    Background monitor for circuit breaker states.
    Can send alerts when breakers trigger.
    """
    
    def __init__(self, circuit_breakers: CircuitBreakers, check_interval_seconds: int = 5):
        self.circuit_breakers = circuit_breakers
        self.check_interval = check_interval_seconds
        self._running = False
        self.alert_callbacks: list[Callable] = []
        
    def add_alert_callback(self, callback: Callable):
        """Add callback to be called when breaker state changes."""
        self.alert_callbacks.append(callback)
    
    def start_monitoring(self, context_provider: Callable):
        """
        Start monitoring circuit breakers.
        
        Args:
            context_provider: Function that returns current context dict
        """
        self._running = True
        print(f"[CircuitBreakerMonitor] Started monitoring")
        
        last_open_breakers = set()
        
        while self._running:
            try:
                # Get current context
                context = context_provider()
                
                # Check all breakers
                allowed, reason = self.circuit_breakers.check_all(context)
                
                # Check for state changes
                current_open = set(self.circuit_breakers.get_open_breakers())
                
                # Newly opened breakers
                newly_opened = current_open - last_open_breakers
                for breaker_name in newly_opened:
                    self._alert_breaker_opened(breaker_name)
                
                # Newly closed breakers
                newly_closed = last_open_breakers - current_open
                for breaker_name in newly_closed:
                    self._alert_breaker_closed(breaker_name)
                
                last_open_breakers = current_open
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[CircuitBreakerMonitor] ERROR: {e}")
                time.sleep(self.check_interval)
    
    def _alert_breaker_opened(self, breaker_name: str):
        """Send alert when breaker opens."""
        message = f"CIRCUIT BREAKER OPENED: {breaker_name}"
        print(f"[CircuitBreakerMonitor] ALERT: {message}")
        
        for callback in self.alert_callbacks:
            try:
                callback("OPENED", breaker_name, message)
            except Exception as e:
                print(f"[CircuitBreakerMonitor] Alert callback error: {e}")
    
    def _alert_breaker_closed(self, breaker_name: str):
        """Send alert when breaker closes."""
        message = f"CIRCUIT BREAKER CLOSED: {breaker_name}"
        print(f"[CircuitBreakerMonitor] ALERT: {message}")
        
        for callback in self.alert_callbacks:
            try:
                callback("CLOSED", breaker_name, message)
            except Exception as e:
                print(f"[CircuitBreakerMonitor] Alert callback error: {e}")
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self._running = False
        print("[CircuitBreakerMonitor] Stopped monitoring")
