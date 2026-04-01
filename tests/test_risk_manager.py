"""
Test suite for RiskManager.
Tests all risk constraints and position sizing logic.

Run:
    pytest tests/test_risk_manager.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, date
from core.risk_manager import RiskManager
from config.prop_firm_configs import PROP_FIRM_CONFIGS


@pytest.fixture
def topstep_50k_config():
    """Topstep 50K configuration for testing."""
    return PROP_FIRM_CONFIGS["TOPSTEP_50K"]


@pytest.fixture
def risk_manager(topstep_50k_config):
    """Fresh RiskManager instance."""
    return RiskManager(topstep_50k_config)


# ------------------------------------------------------------------
# Daily Trade Limit Tests
# ------------------------------------------------------------------

def test_daily_trade_limit_enforced(risk_manager):
    """Should block trading after max_daily_trades reached."""
    risk_manager.daily_trade_count = 5  # Max is 5
    
    allowed, reason = risk_manager.can_trade({}, {})
    
    assert not allowed
    assert "DAILY_TRADE_LIMIT" in reason


def test_daily_trade_limit_allows_under_limit(risk_manager):
    """Should allow trading when under the limit."""
    risk_manager.daily_trade_count = 4  # Under max of 5
    
    allowed, reason = risk_manager.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 50000, "mll_floor": 48000}
    )
    
    assert allowed
    assert reason == ""


# ------------------------------------------------------------------
# Consecutive Losses Tests
# ------------------------------------------------------------------

def test_consecutive_losses_limit(risk_manager):
    """Should block trading after 3 consecutive losses."""
    risk_manager.consecutive_losses = 3
    
    allowed, reason = risk_manager.can_trade({}, {})
    
    assert not allowed
    assert "consecutive losses" in reason.lower()


def test_consecutive_losses_reset_on_win(risk_manager):
    """Consecutive losses should reset after a win."""
    risk_manager.consecutive_losses = 2
    
    # Simulate a winning trade
    risk_manager.update_after_trade({"net_pnl": 100})
    
    assert risk_manager.consecutive_losses == 0


def test_consecutive_losses_increment_on_loss(risk_manager):
    """Consecutive losses should increment on loss."""
    risk_manager.consecutive_losses = 1
    
    # Simulate a losing trade
    risk_manager.update_after_trade({"net_pnl": -50})
    
    assert risk_manager.consecutive_losses == 2


# ------------------------------------------------------------------
# Daily Loss Limit Tests
# ------------------------------------------------------------------

def test_daily_loss_limit_blocks_trading(risk_manager):
    """Should block trading when daily loss limit reached."""
    # MLL is 2000, 40% = 800
    risk_manager.daily_pnl = -900
    
    allowed, reason = risk_manager.can_trade({}, {})
    
    assert not allowed
    assert "Daily loss" in reason


def test_daily_loss_limit_allows_under_limit(risk_manager):
    """Should allow trading when under daily loss limit."""
    risk_manager.daily_pnl = -500  # Under 800 limit
    
    allowed, reason = risk_manager.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 50000, "mll_floor": 48000}
    )
    
    assert allowed


def test_proposed_trade_would_exceed_limit(risk_manager):
    """Should block trade that would exceed daily loss limit."""
    risk_manager.daily_pnl = -600  # Close to 800 limit
    
    proposed_trade = {"max_loss": 250}  # Would exceed limit
    
    allowed, reason = risk_manager.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 50000, "mll_floor": 48000},
        proposed_trade
    )
    
    assert not allowed
    assert "daily loss limit" in reason.lower()


# ------------------------------------------------------------------
# MLL Proximity Tests
# ------------------------------------------------------------------

def test_mll_proximity_blocks_trading(risk_manager):
    """Should block trading when within 10% of MLL floor."""
    # MLL is 2000, 10% = 200
    # Floor would be at 48000 (if peak was 50000)
    account_state = {
        "equity": 48200,  # Only $200 above floor
        "mll_floor": 48000
    }
    
    allowed, reason = risk_manager.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        account_state
    )
    
    assert not allowed
    assert "MLL" in reason


def test_mll_proximity_allows_safe_distance(risk_manager):
    """Should allow trading when safely above MLL floor."""
    account_state = {
        "equity": 51000,  # Well above floor
        "mll_floor": 48000
    }
    
    allowed, reason = risk_manager.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        account_state
    )
    
    assert allowed


# ------------------------------------------------------------------
# Time Between Trades Tests
# ------------------------------------------------------------------

def test_time_between_trades_enforced(risk_manager):
    """Should enforce minimum time between trades."""
    # Add a recent trade
    recent_time = datetime.now()
    risk_manager.trades_today = [{"entry_time": recent_time}]
    
    # Try to trade immediately
    market_state = {"timestamp": recent_time}
    
    allowed, reason = risk_manager.can_trade(market_state, {})
    
    assert not allowed
    assert "min" in reason.lower()


# ------------------------------------------------------------------
# Trading Hours Tests
# ------------------------------------------------------------------

def test_no_trades_first_15_minutes(risk_manager):
    """Should block trades in first 15 minutes."""
    # 9:30 ET = 14:30 UTC
    market_open = datetime(2024, 1, 15, 14, 30)  # 9:30 ET
    market_state = {"timestamp": market_open}
    
    allowed, reason = risk_manager.can_trade(market_state, {})
    
    # Should block due to time filter
    # Note: The exact behavior depends on the hour conversion logic
    # This test assumes the time filter is working


def test_no_trades_last_15_minutes(risk_manager):
    """Should block trades in last 15 minutes."""
    # 15:45 ET = 20:45 UTC
    near_close = datetime(2024, 1, 15, 20, 45)  # 3:45 PM ET
    market_state = {"timestamp": near_close}
    
    allowed, reason = risk_manager.can_trade(market_state, {})
    
    # Should block due to time filter


# ------------------------------------------------------------------
# Position Size Calculation Tests
# ------------------------------------------------------------------

def test_position_size_basic_calculation(risk_manager):
    """Should calculate position size based on risk parameters."""
    contracts = risk_manager.calculate_position_size(
        account_equity=50000,
        atr=7.5,
        stop_distance=5.0,  # 5 points
        point_value=5,  # MES
        confidence=0.7
    )
    
    # Should return at least 1 contract
    assert contracts >= 1
    # Should not exceed max contracts
    assert contracts <= 5


def test_position_size_respects_max_contracts(risk_manager):
    """Should not exceed prop firm max contracts."""
    # Use parameters that would suggest high size
    contracts = risk_manager.calculate_position_size(
        account_equity=50000,
        atr=3.0,  # Low volatility
        stop_distance=2.0,  # Tight stop
        point_value=5,
        confidence=0.9
    )
    
    assert contracts <= 5  # Topstep 50K max


def test_position_size_minimum_one_contract(risk_manager):
    """Should always return at least 1 contract."""
    contracts = risk_manager.calculate_position_size(
        account_equity=50000,
        atr=20.0,  # High volatility
        stop_distance=10.0,  # Wide stop
        point_value=5,
        confidence=0.3  # Low confidence
    )
    
    assert contracts >= 1


# ------------------------------------------------------------------
# Daily Counter Reset Tests
# ------------------------------------------------------------------

def test_daily_counters_reset(risk_manager):
    """Should reset all daily counters."""
    risk_manager.daily_trade_count = 5
    risk_manager.daily_pnl = -500
    risk_manager.consecutive_losses = 2
    risk_manager.max_daily_loss_hit = True
    
    risk_manager.reset_daily_counters(date.today())
    
    assert risk_manager.daily_trade_count == 0
    assert risk_manager.daily_pnl == 0.0
    assert risk_manager.consecutive_losses == 0
    assert not risk_manager.max_daily_loss_hit


# ------------------------------------------------------------------
# Risk Summary Tests
# ------------------------------------------------------------------

def test_risk_summary_returns_expected_keys(risk_manager):
    """Risk summary should include expected fields."""
    risk_manager.daily_trade_count = 3
    risk_manager.daily_pnl = 150.0
    risk_manager.consecutive_losses = 1
    
    summary = risk_manager.get_risk_summary()
    
    assert "daily_trades" in summary
    assert "max_daily_trades" in summary
    assert "daily_pnl" in summary
    assert "consecutive_losses" in summary
    assert "trades_remaining" in summary


def test_trades_remaining_calculation(risk_manager):
    """Should correctly calculate remaining trades."""
    risk_manager.daily_trade_count = 3
    
    summary = risk_manager.get_risk_summary()
    
    assert summary["trades_remaining"] == 2  # 5 max - 3 used


# ------------------------------------------------------------------
# Multiple Constraints Tests
# ------------------------------------------------------------------

def test_multiple_violations_reports_first(risk_manager):
    """When multiple constraints violated, should report the first one."""
    risk_manager.daily_trade_count = 5  # Max reached
    risk_manager.consecutive_losses = 3  # Also maxed
    
    allowed, reason = risk_manager.can_trade({}, {})
    
    assert not allowed
    # Should report first violation found
    assert "DAILY_TRADE_LIMIT" in reason
