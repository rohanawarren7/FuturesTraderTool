"""
Test suite for PositionSizer.
Tests volatility-adjusted position sizing and Kelly criterion calculations.

Run:
    pytest tests/test_position_sizer.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import numpy as np
from core.position_sizer import PositionSizer
from core.risk_manager import RiskManager
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS


@pytest.fixture
def topstep_config():
    """Topstep 50K configuration."""
    return PROP_FIRM_CONFIGS["TOPSTEP_50K"]


@pytest.fixture
def mes_specs():
    """MES instrument specifications."""
    return INSTRUMENT_SPECS["MES"]


@pytest.fixture
def risk_manager(topstep_config):
    """RiskManager instance."""
    return RiskManager(topstep_config)


@pytest.fixture
def position_sizer(risk_manager, mes_specs):
    """PositionSizer instance."""
    return PositionSizer(risk_manager, mes_specs)


# ------------------------------------------------------------------
# Basic Position Size Calculation Tests
# ------------------------------------------------------------------

def test_calculate_size_returns_dict(position_sizer):
    """Should return a dictionary with sizing information."""
    result = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=7.5,
        signal_confidence=0.7
    )
    
    assert isinstance(result, dict)
    assert "contracts" in result
    assert "risk_dollars" in result
    assert "risk_pct" in result


def test_calculate_size_minimum_one_contract(position_sizer):
    """Should always return at least 1 contract."""
    result = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4990.0,  # Wide stop = high risk
        atr=15.0,  # High volatility
        signal_confidence=0.3  # Low confidence
    )
    
    assert result["contracts"] >= 1


def test_calculate_size_respects_max_contracts(position_sizer):
    """Should not exceed max contracts from prop firm config."""
    result = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4999.0,  # Tight stop
        atr=3.0,  # Low volatility
        signal_confidence=0.9  # High confidence
    )
    
    # Topstep 50K max is 5 contracts
    assert result["contracts"] <= 5


def test_calculate_size_risk_under_one_percent(position_sizer):
    """Risk should be around 1% of equity."""
    result = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=7.5,
        signal_confidence=0.7
    )
    
    # Risk should be approximately 1% or less
    assert result["risk_pct"] <= 1.5  # Allow some variance


# ------------------------------------------------------------------
# Volatility Adjustment Tests
# ------------------------------------------------------------------

def test_high_volatility_reduces_size(position_sizer):
    """High ATR should result in smaller position size."""
    # Low volatility scenario
    result_low_vol = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=3.0,  # Low vol
        signal_confidence=0.7
    )
    
    # High volatility scenario
    result_high_vol = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=15.0,  # High vol
        signal_confidence=0.7
    )
    
    # High vol should have fewer or equal contracts
    assert result_high_vol["contracts"] <= result_low_vol["contracts"]


def test_volatility_adjustment_range(position_sizer):
    """Volatility adjustment should be between 0.5 and 1.5."""
    adjustment_low = position_sizer._calculate_volatility_adjustment(3.0, "BALANCED")
    adjustment_mid = position_sizer._calculate_volatility_adjustment(7.5, "BALANCED")
    adjustment_high = position_sizer._calculate_volatility_adjustment(15.0, "BALANCED")
    
    # All should be within reasonable bounds
    assert 0.5 <= adjustment_low <= 1.5
    assert 0.5 <= adjustment_mid <= 1.5
    assert 0.5 <= adjustment_high <= 1.5
    
    # Low vol should have higher adjustment
    assert adjustment_low >= adjustment_high


# ------------------------------------------------------------------
# Confidence Adjustment Tests
# ------------------------------------------------------------------

def test_high_confidence_increases_size(position_sizer):
    """High confidence should result in larger position size."""
    result_low_conf = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=7.5,
        signal_confidence=0.5
    )
    
    result_high_conf = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=7.5,
        signal_confidence=0.9
    )
    
    # High confidence should have more or equal contracts
    assert result_high_conf["contracts"] >= result_low_conf["contracts"]


def test_confidence_adjustment_bounds(position_sizer):
    """Confidence adjustment should respect min/max bounds."""
    adj_low = position_sizer._calculate_confidence_adjustment(0.0)
    adj_mid = position_sizer._calculate_confidence_adjustment(0.5)
    adj_high = position_sizer._calculate_confidence_adjustment(1.0)
    
    # Minimum should be 0.5
    assert adj_low == 0.5
    
    # Maximum should be 1.0
    assert adj_high == 1.0
    
    # Mid should be 0.5
    assert adj_mid == 0.5


# ------------------------------------------------------------------
# Market State Adjustment Tests
# ------------------------------------------------------------------

def test_balanced_market_full_size(position_sizer):
    """Balanced market should allow full position size."""
    adj = position_sizer._calculate_state_adjustment("BALANCED")
    assert adj == 1.0


def test_volatile_transition_reduces_size(position_sizer):
    """Volatile transition should reduce position size."""
    adj = position_sizer._calculate_state_adjustment("VOLATILE_TRANS")
    assert adj == 0.7


def test_low_activity_significantly_reduces_size(position_sizer):
    """Low activity should significantly reduce position size."""
    adj = position_sizer._calculate_state_adjustment("LOW_ACTIVITY")
    assert adj == 0.5


def test_imbalanced_markets_full_size(position_sizer):
    """Imbalanced markets should allow full position size."""
    adj_bull = position_sizer._calculate_state_adjustment("IMBALANCED_BULL")
    adj_bear = position_sizer._calculate_state_adjustment("IMBALANCED_BEAR")
    
    assert adj_bull == 1.0
    assert adj_bear == 1.0


# ------------------------------------------------------------------
# Position Validation Tests
# ------------------------------------------------------------------

def test_validate_position_checks_max_contracts(position_sizer):
    """Should reject positions exceeding max contracts."""
    valid, reason = position_sizer.validate_position(
        proposed_size=10,  # Exceeds Topstep 50K max of 5
        account_equity=50000,
        current_positions=[]
    )
    
    assert not valid
    assert "max contracts" in reason.lower()


def test_validate_position_checks_concurrent_positions(position_sizer):
    """Should reject if max concurrent positions reached."""
    current_positions = [{"contracts": 1}]
    
    valid, reason = position_sizer.validate_position(
        proposed_size=1,
        account_equity=50000,
        current_positions=current_positions
    )
    
    # Should pass with 1 position (max is 1)
    # If we add another, it should fail
    current_positions.append({"contracts": 1})
    
    valid, reason = position_sizer.validate_position(
        proposed_size=1,
        account_equity=50000,
        current_positions=current_positions
    )
    
    assert not valid


def test_validate_position_allows_valid_position(position_sizer):
    """Should accept valid position sizes."""
    valid, reason = position_sizer.validate_position(
        proposed_size=2,
        account_equity=50000,
        current_positions=[]
    )
    
    assert valid
    assert reason == ""


# ------------------------------------------------------------------
# Kelly Criterion Tests
# ------------------------------------------------------------------

def test_kelly_calculation_basic(position_sizer):
    """Should calculate Kelly fraction correctly."""
    kelly = position_sizer.calculate_kelly_fraction(
        win_rate=0.6,
        avg_win=200,
        avg_loss=100
    )
    
    # Kelly = (0.6 * 2 - 0.4) / 2 = 0.4
    # Half-Kelly = 0.2
    assert 0.15 <= kelly <= 0.25


def test_kelly_handles_edge_cases(position_sizer):
    """Should handle edge cases gracefully."""
    # Zero avg loss
    kelly1 = position_sizer.calculate_kelly_fraction(0.6, 200, 0)
    assert kelly1 == 0.0
    
    # Zero win rate
    kelly2 = position_sizer.calculate_kelly_fraction(0, 200, 100)
    assert kelly2 == 0.0
    
    # 100% win rate
    kelly3 = position_sizer.calculate_kelly_fraction(1.0, 200, 100)
    # Should be capped at reasonable value
    assert kelly3 <= 0.25


def test_kelly_capped_at_quarter(position_sizer):
    """Half-Kelly should be capped at 25%."""
    # Very favorable odds
    kelly = position_sizer.calculate_kelly_fraction(
        win_rate=0.8,
        avg_win=500,
        avg_loss=100
    )
    
    assert kelly <= 0.25


# ------------------------------------------------------------------
# Breathing Room Stop Tests
# ------------------------------------------------------------------

def test_breathing_room_increases_stop_distance(position_sizer):
    """Should increase stop distance to provide breathing room."""
    entry = 5000.0
    initial_stop = 4999.0  # Only 1 point away
    vwap_std = 2.0  # VWAP standard deviation
    
    adjusted_stop = position_sizer.get_breathing_room_stop(
        entry_price=entry,
        stop_price=initial_stop,
        vwap_std=vwap_std
    )
    
    # Should be further away than initial stop
    assert adjusted_stop < initial_stop  # For long position
    assert (entry - adjusted_stop) >= (vwap_std * 1.5)


def test_breathing_room_respects_existing_wide_stop(position_sizer):
    """Should not adjust if stop already has breathing room."""
    entry = 5000.0
    initial_stop = 4990.0  # 10 points away
    vwap_std = 2.0
    
    adjusted_stop = position_sizer.get_breathing_room_stop(
        entry_price=entry,
        stop_price=initial_stop,
        vwap_std=vwap_std
    )
    
    # Should keep original stop
    assert adjusted_stop == initial_stop


# ------------------------------------------------------------------
# Correlation Adjustment Tests
# ------------------------------------------------------------------

def test_correlation_reduces_size(position_sizer):
    """Should reduce size if already exposed to correlated instruments."""
    base_size = 5
    correlated_exposure = 0.8  # 80% exposed
    
    adjusted = position_sizer.adjust_for_correlation(
        base_size=base_size,
        correlated_exposure=correlated_exposure
    )
    
    # Should be reduced
    assert adjusted < base_size
    assert adjusted >= 1


def test_no_correlation_unchanged(position_sizer):
    """Should not change size if no correlation."""
    base_size = 5
    correlated_exposure = 0.0
    
    adjusted = position_sizer.adjust_for_correlation(
        base_size=base_size,
        correlated_exposure=correlated_exposure
    )
    
    assert adjusted == base_size


# ------------------------------------------------------------------
# Integration Tests
# ------------------------------------------------------------------

def test_full_sizing_workflow(position_sizer):
    """Test complete position sizing workflow."""
    result = position_sizer.calculate_size(
        account_equity=50000,
        entry_price=5000.0,
        stop_price=4995.0,
        atr=7.5,
        signal_confidence=0.75,
        market_state="BALANCED"
    )
    
    # Verify all components are present
    assert "contracts" in result
    assert "risk_dollars" in result
    assert "risk_pct" in result
    assert "position_value" in result
    assert "volatility_adjustment" in result
    assert "confidence_adjustment" in result
    assert "state_adjustment" in result
    
    # Verify values are reasonable
    assert result["contracts"] >= 1
    assert result["contracts"] <= 5
    assert result["risk_pct"] > 0
    assert result["risk_pct"] <= 2.0
