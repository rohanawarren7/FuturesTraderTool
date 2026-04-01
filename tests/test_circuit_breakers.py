"""
Test suite for CircuitBreakers.
Tests all circuit breaker conditions and state management.

Run:
    pytest tests/test_circuit_breakers.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta
from execution.circuit_breakers import CircuitBreaker, CircuitBreakers, CircuitBreakerState
from config.prop_firm_configs import PROP_FIRM_CONFIGS


@pytest.fixture
def topstep_config():
    """Topstep 50K configuration."""
    return PROP_FIRM_CONFIGS["TOPSTEP_50K"]


@pytest.fixture
def circuit_breakers(topstep_config):
    """CircuitBreakers instance."""
    return CircuitBreakers(topstep_config)


@pytest.fixture
def simple_breaker():
    """Simple circuit breaker for testing."""
    def check_func(context):
        return context.get("trigger", False), "Test trigger"
    
    return CircuitBreaker(
        name="test",
        check_func=check_func,
        reset_timeout_seconds=1,
        auto_reset=True
    )


# ------------------------------------------------------------------
# Circuit Breaker State Tests
# ------------------------------------------------------------------

def test_initial_state_is_closed(simple_breaker):
    """Circuit breaker should start in CLOSED state."""
    assert simple_breaker.state == CircuitBreakerState.CLOSED


def test_trigger_opens_breaker(simple_breaker):
    """Triggering should open the breaker."""
    simple_breaker._trigger("Test reason")
    
    assert simple_breaker.state == CircuitBreakerState.OPEN
    assert simple_breaker.trigger_count == 1


def test_open_breaker_blocks(simple_breaker):
    """Open breaker should block."""
    simple_breaker._trigger("Test")
    
    blocked, reason = simple_breaker.check({"trigger": False})
    
    assert blocked is True
    assert "OPEN" in reason


def test_manual_reset(simple_breaker):
    """Manual reset should close the breaker."""
    simple_breaker._trigger("Test")
    simple_breaker.manual_reset()
    
    assert simple_breaker.state == CircuitBreakerState.CLOSED


def test_get_status_returns_dict(simple_breaker):
    """Should return status dictionary."""
    status = simple_breaker.get_status()
    
    assert "name" in status
    assert "state" in status
    assert "trigger_count" in status


# ------------------------------------------------------------------
# Daily Loss Breaker Tests
# ------------------------------------------------------------------

def test_daily_loss_breaker_triggers(circuit_breakers):
    """Should trigger when daily loss exceeds 40% of MLL."""
    # MLL is 2000, 40% = 800
    context = {"daily_pnl": -900}
    
    breaker = circuit_breakers.breakers["daily_loss"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True
    assert "900" in reason


def test_daily_loss_breaker_no_trigger(circuit_breakers):
    """Should not trigger when daily loss is under limit."""
    context = {"daily_pnl": -500}  # Under 800 limit
    
    breaker = circuit_breakers.breakers["daily_loss"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# MLL Proximity Breaker Tests
# ------------------------------------------------------------------

def test_mll_proximity_breaker_triggers(circuit_breakers):
    """Should trigger when within 10% of MLL floor."""
    # MLL is 2000, 10% = 200
    context = {
        "equity": 48200,  # $200 above floor
        "mll_floor": 48000
    }
    
    breaker = circuit_breakers.breakers["mll_proximity"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


def test_mll_proximity_breaker_no_trigger(circuit_breakers):
    """Should not trigger when safely above floor."""
    context = {
        "equity": 51000,  # Well above floor
        "mll_floor": 48000
    }
    
    breaker = circuit_breakers.breakers["mll_proximity"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# Consecutive Losses Breaker Tests
# ------------------------------------------------------------------

def test_consecutive_losses_breaker_triggers(circuit_breakers):
    """Should trigger after 3 consecutive losses."""
    context = {"consecutive_losses": 3}
    
    breaker = circuit_breakers.breakers["consecutive_losses"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


def test_consecutive_losses_breaker_no_trigger(circuit_breakers):
    """Should not trigger with fewer than 3 losses."""
    context = {"consecutive_losses": 2}
    
    breaker = circuit_breakers.breakers["consecutive_losses"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# Data Freshness Breaker Tests
# ------------------------------------------------------------------

def test_data_freshness_breaker_triggers(circuit_breakers):
    """Should trigger when data is stale."""
    old_time = datetime.now() - timedelta(seconds=120)
    context = {"last_data_timestamp": old_time}
    
    breaker = circuit_breakers.breakers["data_freshness"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


def test_data_freshness_breaker_no_trigger(circuit_breakers):
    """Should not trigger with fresh data."""
    recent_time = datetime.now() - timedelta(seconds=10)
    context = {"last_data_timestamp": recent_time}
    
    breaker = circuit_breakers.breakers["data_freshness"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# Broker Connectivity Breaker Tests
# ------------------------------------------------------------------

def test_broker_connectivity_breaker_triggers(circuit_breakers):
    """Should trigger when broker is unresponsive."""
    old_time = datetime.now() - timedelta(seconds=30)
    context = {"last_broker_ping": old_time}
    
    breaker = circuit_breakers.breakers["broker_connectivity"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


# ------------------------------------------------------------------
# Order Rate Breaker Tests
# ------------------------------------------------------------------

def test_order_rate_breaker_triggers(circuit_breakers):
    """Should trigger with too many recent orders."""
    now = datetime.now()
    recent_orders = [now - timedelta(seconds=i*5) for i in range(15)]
    context = {"recent_orders": recent_orders}
    
    breaker = circuit_breakers.breakers["order_rate"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


def test_order_rate_breaker_no_trigger(circuit_breakers):
    """Should not trigger with normal order rate."""
    now = datetime.now()
    recent_orders = [now - timedelta(seconds=i*10) for i in range(3)]
    context = {"recent_orders": recent_orders}
    
    breaker = circuit_breakers.breakers["order_rate"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# Adverse Skew Breaker Tests
# ------------------------------------------------------------------

def test_adverse_skew_breaker_triggers(circuit_breakers):
    """Should trigger with high slippage."""
    fills = [
        {"slippage_ticks": 3},
        {"slippage_ticks": 3},
        {"slippage_ticks": 3},
        {"slippage_ticks": 3},
        {"slippage_ticks": 3},
    ]
    context = {"recent_fills": fills}
    
    breaker = circuit_breakers.breakers["adverse_skew"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


def test_adverse_skew_breaker_no_trigger(circuit_breakers):
    """Should not trigger with normal slippage."""
    fills = [
        {"slippage_ticks": 1},
        {"slippage_ticks": 1},
        {"slippage_ticks": 1},
    ]
    context = {"recent_fills": fills}
    
    breaker = circuit_breakers.breakers["adverse_skew"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is False


# ------------------------------------------------------------------
# Technical Failure Breaker Tests
# ------------------------------------------------------------------

def test_technical_failure_breaker_triggers(circuit_breakers):
    """Should trigger with multiple recent errors."""
    now = datetime.now()
    errors = [
        {"timestamp": (now - timedelta(minutes=1)).isoformat()},
        {"timestamp": (now - timedelta(minutes=2)).isoformat()},
        {"timestamp": (now - timedelta(minutes=3)).isoformat()},
    ]
    context = {"recent_errors": errors}
    
    breaker = circuit_breakers.breakers["technical_failure"]
    triggered, reason = breaker.check_func(context)
    
    assert triggered is True


# ------------------------------------------------------------------
# Circuit Breakers Manager Tests
# ------------------------------------------------------------------

def test_check_all_passes(circuit_breakers):
    """Should allow trading when all breakers pass."""
    context = {
        "daily_pnl": 100,
        "equity": 51000,
        "mll_floor": 48000,
        "consecutive_losses": 0,
        "last_data_timestamp": datetime.now(),
        "last_broker_ping": datetime.now(),
        "recent_orders": [],
        "recent_fills": [],
        "recent_errors": [],
    }
    
    allowed, reason = circuit_breakers.check_all(context)
    
    assert allowed is True
    assert reason == ""


def test_check_all_fails(circuit_breakers):
    """Should block trading when any breaker fails."""
    context = {
        "daily_pnl": -1000,  # Exceeds limit
        "equity": 51000,
        "mll_floor": 48000,
        "consecutive_losses": 0,
        "last_data_timestamp": datetime.now(),
        "last_broker_ping": datetime.now(),
        "recent_orders": [],
        "recent_fills": [],
        "recent_errors": [],
    }
    
    allowed, reason = circuit_breakers.check_all(context)
    
    assert allowed is False
    assert "daily_loss" in reason.lower()


def test_get_open_breakers(circuit_breakers):
    """Should return list of open breakers."""
    # Trigger a breaker
    circuit_breakers.breakers["daily_loss"]._trigger("Test")
    
    open_breakers = circuit_breakers.get_open_breakers()
    
    assert "daily_loss" in open_breakers


def test_manual_reset_single(circuit_breakers):
    """Should reset single breaker."""
    circuit_breakers.breakers["daily_loss"]._trigger("Test")
    circuit_breakers.manual_reset("daily_loss")
    
    assert circuit_breakers.breakers["daily_loss"].state == CircuitBreakerState.CLOSED


def test_manual_reset_all(circuit_breakers):
    """Should reset all breakers."""
    # Trigger multiple breakers
    circuit_breakers.breakers["daily_loss"]._trigger("Test")
    circuit_breakers.breakers["mll_proximity"]._trigger("Test")
    
    circuit_breakers.manual_reset(None)  # Reset all
    
    for breaker in circuit_breakers.breakers.values():
        assert breaker.state == CircuitBreakerState.CLOSED


# ------------------------------------------------------------------
# Circuit Breaker Status Tests
# ------------------------------------------------------------------

def test_get_status_returns_all_breakers(circuit_breakers):
    """Should return status for all breakers."""
    status = circuit_breakers.get_status()
    
    assert "daily_loss" in status
    assert "mll_proximity" in status
    assert "consecutive_losses" in status
    assert len(status) == 8  # All breakers


def test_status_contains_required_fields(circuit_breakers):
    """Status should contain required fields."""
    status = circuit_breakers.get_status()
    
    for breaker_status in status.values():
        assert "name" in breaker_status
        assert "state" in breaker_status
        assert "trigger_count" in breaker_status
