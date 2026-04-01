"""
Test suite for PositionSynchronizer.
Tests position synchronization and emergency flatten functionality.

Run:
    pytest tests/test_position_sync.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock
from execution.position_sync import Position, PositionSynchronizer


@pytest.fixture
def mock_provider():
    """Mock TradovateDataProvider."""
    provider = Mock()
    provider.base_url = "https://demo.tradovateapi.com/v1"
    provider._headers.return_value = {"Authorization": "Bearer test_token"}
    return provider


@pytest.fixture
def mock_db():
    """Mock DBManager."""
    db = Mock()
    db.get_recent_live_trades.return_value = []
    db.insert_live_trade.return_value = 1
    return db


@pytest.fixture
def position_sync(mock_provider, mock_db):
    """PositionSynchronizer instance with mocks."""
    return PositionSynchronizer(mock_provider, mock_db)


# ------------------------------------------------------------------
# Position Dataclass Tests
# ------------------------------------------------------------------

def test_position_is_long():
    """Position should correctly identify long positions."""
    pos = Position(
        instrument="MES",
        quantity=2,
        avg_price=5000.0,
        unrealized_pnl=100.0,
        realized_pnl=0.0
    )
    
    assert pos.is_long is True
    assert pos.is_short is False
    assert pos.is_flat is False


def test_position_is_short():
    """Position should correctly identify short positions."""
    pos = Position(
        instrument="MES",
        quantity=-2,
        avg_price=5000.0,
        unrealized_pnl=100.0,
        realized_pnl=0.0
    )
    
    assert pos.is_long is False
    assert pos.is_short is True
    assert pos.is_flat is False


def test_position_is_flat():
    """Position should correctly identify flat positions."""
    pos = Position(
        instrument="MES",
        quantity=0,
        avg_price=0.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0
    )
    
    assert pos.is_long is False
    assert pos.is_short is False
    assert pos.is_flat is True


# ------------------------------------------------------------------
# Position Status Tests
# ------------------------------------------------------------------

def test_get_position_status_returns_dict(position_sync):
    """Should return position status as dictionary."""
    status = position_sync.get_position_status()
    
    assert isinstance(status, dict)
    assert "broker_position" in status
    assert "local_position" in status
    assert "synced" in status


def test_positions_match_when_both_flat(position_sync):
    """Positions should match when both are flat."""
    position_sync.broker_position = None
    position_sync.local_position = None
    
    assert position_sync._positions_match() is True


def test_positions_mismatch_when_one_has_position(position_sync):
    """Positions should not match when one has position and other doesn't."""
    position_sync.broker_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    position_sync.local_position = None
    
    assert position_sync._positions_match() is False


# ------------------------------------------------------------------
# Discrepancy Detection Tests
# ------------------------------------------------------------------

def test_detect_missing_local_position(position_sync):
    """Should detect when broker has position but local doesn't."""
    position_sync.broker_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    position_sync.local_position = None
    
    discrepancies = position_sync._detect_discrepancies()
    
    assert len(discrepancies) == 1
    assert discrepancies[0]["type"] == "MISSING_LOCAL_POSITION"


def test_detect_missing_broker_position(position_sync):
    """Should detect when local has position but broker doesn't."""
    position_sync.broker_position = None
    position_sync.local_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    
    discrepancies = position_sync._detect_discrepancies()
    
    assert len(discrepancies) == 1
    assert discrepancies[0]["type"] == "MISSING_BROKER_POSITION"


def test_detect_quantity_mismatch(position_sync):
    """Should detect quantity mismatches."""
    position_sync.broker_position = Position(
        instrument="MES", quantity=3, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    position_sync.local_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    
    discrepancies = position_sync._detect_discrepancies()
    
    assert len(discrepancies) == 1
    assert discrepancies[0]["type"] == "QUANTITY_MISMATCH"


def test_no_discrepancy_when_matching(position_sync):
    """Should not detect discrepancies when positions match."""
    position_sync.broker_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    position_sync.local_position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    
    discrepancies = position_sync._detect_discrepancies()
    
    assert len(discrepancies) == 0


# ------------------------------------------------------------------
# Emergency Flatten Tests
# ------------------------------------------------------------------

def test_emergency_flatten_already_flat(position_sync):
    """Should handle already flat position."""
    position_sync._fetch_broker_position = Mock(return_value=None)
    
    result = position_sync.emergency_flatten("Test reason")
    
    assert result["success"] is True
    assert "Already flat" in result.get("message", "")


def test_emergency_flatten_creates_order(position_sync):
    """Should create flatten order when in position."""
    position = Position(
        instrument="MES", quantity=2, avg_price=5000.0,
        unrealized_pnl=0.0, realized_pnl=0.0
    )
    position_sync._fetch_broker_position = Mock(return_value=position)
    
    result = position_sync.emergency_flatten("Test reason")
    
    assert result["reason"] == "Test reason"


# ------------------------------------------------------------------
# Position to Dict Tests
# ------------------------------------------------------------------

def test_position_to_dict_with_position(position_sync):
    """Should convert Position to dictionary."""
    pos = Position(
        instrument="MES",
        quantity=2,
        avg_price=5000.0,
        unrealized_pnl=100.0,
        realized_pnl=50.0
    )
    
    result = position_sync._position_to_dict(pos)
    
    assert result["instrument"] == "MES"
    assert result["quantity"] == 2
    assert result["avg_price"] == 5000.0
    assert result["is_long"] is True
    assert result["is_flat"] is False


def test_position_to_dict_none(position_sync):
    """Should handle None position."""
    result = position_sync._position_to_dict(None)
    
    assert result is None
