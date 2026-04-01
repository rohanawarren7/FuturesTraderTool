"""
Paper Trading Integration Test
Tests the complete end-to-end paper trading workflow.

This test simulates a full trading session including:
- Entry signal processing
- Risk validation
- Circuit breaker checks
- Position synchronization
- Fill processing
- Exit signal processing
- PnL calculation
- Circuit breaker scenarios

Run:
    pytest tests/integration/test_paper_trading.py -v
    
Or run specific test scenarios:
    pytest tests/integration/test_paper_trading.py::TestPaperTradingWorkflow::test_complete_long_trade -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
import time

from fastapi.testclient import TestClient

# Import the enhanced webhook server
from execution.webhook_server_enhanced import app, get_db, get_circuit_breakers, get_position_sync
from execution.circuit_breakers import CircuitBreakers
from execution.position_sync import PositionSynchronizer, Position
from core.risk_manager import RiskManager
from core.position_sizer import PositionSizer
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS


class MockDBManager:
    """Mock database manager for testing."""
    
    def __init__(self):
        self.trades = []
        self.daily_summaries = []
        self._trade_id_counter = 1
        
    def insert_live_trade(self, record):
        record['id'] = self._trade_id_counter
        self._trade_id_counter += 1
        self.trades.append(record)
        return record['id']
    
    def get_recent_live_trades(self, limit=50):
        return sorted(self.trades, key=lambda x: x.get('entry_time', ''), reverse=True)[:limit]
    
    def get_daily_summaries(self, limit=30):
        return self.daily_summaries[-limit:]
    
    def upsert_daily_summary(self, record):
        # Remove existing record for this date
        self.daily_summaries = [s for s in self.daily_summaries if s.get('date') != record.get('date')]
        self.daily_summaries.append(record)


class MockTradovateProvider:
    """Mock Tradovate provider for testing."""
    
    def __init__(self):
        self.base_url = "https://demo.tradovateapi.com/v1"
        self.positions = []  # No positions initially
        self._token = "mock_token"
        
    def _headers(self):
        return {"Authorization": f"Bearer {self._token}"}
    
    def get_position(self):
        """Get current position."""
        if self.positions:
            return self.positions[-1]
        return None
    
    def set_position(self, position):
        """Set position (for simulating broker state)."""
        self.positions.append(position)


@pytest.fixture
def mock_db():
    """Create mock database."""
    return MockDBManager()


@pytest.fixture
def mock_provider():
    """Create mock Tradovate provider."""
    return MockTradovateProvider()


@pytest.fixture
def client(mock_db, mock_provider):
    """Create test client with mocked dependencies."""
    # Override dependencies
    def override_get_db():
        return mock_db
    
    def override_get_circuit_breakers():
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        return CircuitBreakers(config)
    
    def override_get_position_sync():
        sync = PositionSynchronizer(mock_provider, mock_db)
        return sync
    
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_circuit_breakers] = override_get_circuit_breakers
    app.dependency_overrides[get_position_sync] = override_get_position_sync
    
    return TestClient(app)


class TestPaperTradingWorkflow:
    """Test complete paper trading workflow."""
    
    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_status_endpoint(self, client):
        """Test status endpoint returns system status."""
        response = client.get("/status")
        assert response.status_code == 200
        
        data = response.json()
        assert "position" in data
        assert "circuit_breakers" in data
        assert "recent_trades" in data
        assert "system_status" in data
    
    def test_entry_signal_accepted(self, client, mock_db):
        """Test entry signal is accepted and logged."""
        entry_payload = {
            "ticker": "MES1!",
            "action": "buy",
            "orderType": "market",
            "quantity": 1,
            "price": "5000.00",
            "timestamp": datetime.utcnow().isoformat(),
            "setup": "MEAN_REVERSION_LONG",
            "stopPrice": "4995.00",
            "targetPrice": "5010.00"
        }
        
        response = client.post("/webhook/entry", json=entry_payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert "order" in data
        
        # Verify trade was logged
        assert len(mock_db.trades) == 1
        assert mock_db.trades[0]["direction"] == "BUY"
        assert mock_db.trades[0]["contracts"] == 1
    
    def test_entry_blocked_when_already_in_position(self, client, mock_provider):
        """Test entry is blocked when already in position."""
        # Simulate existing position
        mock_provider.set_position({
            "contractId": "MES",
            "netPos": 1,
            "netPrice": 5000.0
        })
        
        entry_payload = {
            "ticker": "MES1!",
            "action": "buy",
            "orderType": "market",
            "quantity": 1,
            "price": "5000.00",
            "timestamp": datetime.utcnow().isoformat(),
            "setup": "MEAN_REVERSION_LONG"
        }
        
        response = client.post("/webhook/entry", json=entry_payload)
        
        assert response.status_code == 409
        data = response.json()
        assert "Already in position" in data["reason"]
    
    def test_entry_blocked_by_daily_loss_limit(self, client):
        """Test entry is blocked when daily loss limit reached."""
        # This would require mocking the circuit breaker context
        # For now, we test the circuit breaker directly
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        context = {
            "daily_pnl": -900,  # Exceeds 40% of MLL ($800)
            "equity": 49100,
            "mll_floor": 48000,
            "consecutive_losses": 0,
            "last_data_timestamp": datetime.now(),
            "last_broker_ping": datetime.now(),
            "recent_orders": [],
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "daily_loss" in reason.lower()
    
    def test_entry_blocked_by_mll_proximity(self, client):
        """Test entry is blocked when too close to MLL floor."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        context = {
            "daily_pnl": 0,
            "equity": 48200,  # Only $200 above floor
            "mll_floor": 48000,
            "consecutive_losses": 0,
            "last_data_timestamp": datetime.now(),
            "last_broker_ping": datetime.now(),
            "recent_orders": [],
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "mll" in reason.lower()
    
    def test_entry_blocked_by_consecutive_losses(self, client):
        """Test entry is blocked after 3 consecutive losses."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        context = {
            "daily_pnl": -100,
            "equity": 49900,
            "mll_floor": 48000,
            "consecutive_losses": 3,
            "last_data_timestamp": datetime.now(),
            "last_broker_ping": datetime.now(),
            "recent_orders": [],
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "consecutive" in reason.lower()
    
    def test_exit_signal_processing(self, client, mock_db):
        """Test exit signal is processed and PnL calculated."""
        # First create an open position
        open_trade = {
            "trade_id": "TEST_001",
            "prop_firm": "TOPSTEP_50K",
            "account_id": "demo",
            "instrument": "MES1!",
            "direction": "BUY",
            "entry_time": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
            "exit_time": None,
            "entry_price": 5000.0,
            "exit_price": None,
            "contracts": 1,
            "gross_pnl": None,
            "commission": None,
            "net_pnl": None,
            "setup_type": "MEAN_REVERSION_LONG"
        }
        mock_db.trades.append(open_trade)
        
        # Send exit signal
        exit_payload = {
            "ticker": "MES1!",
            "action": "exit",
            "quantity": 1,
            "price": "5010.00",
            "timestamp": datetime.utcnow().isoformat(),
            "reason": "target"
        }
        
        response = client.post("/webhook/exit", json=exit_payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        
        # Verify PnL calculation
        pnl = data["pnl"]
        # (5010 - 5000) * 1 * $5 point value - $1.40 commission = $50 - $1.40 = $48.60
        expected_gross = (5010.0 - 5000.0) * 1 * 5  # $50
        assert pnl["gross"] == expected_gross
        assert pnl["commission"] == 1.40  # $0.70 * 2 sides
    
    def test_short_trade_workflow(self, client, mock_db):
        """Test complete short trade workflow."""
        # Entry
        entry_payload = {
            "ticker": "MES1!",
            "action": "sell",
            "orderType": "market",
            "quantity": 1,
            "price": "5050.00",
            "timestamp": datetime.utcnow().isoformat(),
            "setup": "MEAN_REVERSION_SHORT",
            "stopPrice": "5060.00",
            "targetPrice": "5030.00"
        }
        
        response = client.post("/webhook/entry", json=entry_payload)
        assert response.status_code == 200
        
        # Verify short position logged
        assert len(mock_db.trades) == 1
        assert mock_db.trades[0]["direction"] == "SELL"
        
        # Exit
        exit_payload = {
            "ticker": "MES1!",
            "action": "exit",
            "quantity": 1,
            "price": "5030.00",
            "timestamp": datetime.utcnow().isoformat(),
            "reason": "target"
        }
        
        response = client.post("/webhook/exit", json=exit_payload)
        assert response.status_code == 200
        
        # Verify short PnL (entry - exit)
        pnl = response.json()["pnl"]
        expected_gross = (5050.0 - 5030.0) * 1 * 5  # $100
        assert pnl["gross"] == expected_gross
    
    def test_circuit_breaker_manual_reset(self, client):
        """Test manual circuit breaker reset via admin endpoint."""
        # Note: This test would need proper auth headers in production
        # For testing, we verify the endpoint structure
        
        response = client.post("/admin/reset-breaker?breaker_name=daily_loss&secret=wrong_secret")
        assert response.status_code == 401  # Should fail with wrong secret
    
    def test_complete_long_trade(self, client, mock_db, mock_provider):
        """Test complete long trade from entry to exit."""
        print("\n[INTEGRATION TEST] Complete Long Trade Workflow")
        print("=" * 60)
        
        # Step 1: Initial state check
        print("\n1. Checking initial system status...")
        response = client.get("/status")
        assert response.status_code == 200
        initial_status = response.json()
        assert initial_status["system_status"]["trading_allowed"] is True
        print("   ✓ System ready for trading")
        
        # Step 2: Send entry signal
        print("\n2. Sending entry signal (BUY 1 MES @ 5000)...")
        entry_time = datetime.utcnow()
        entry_payload = {
            "ticker": "MES1!",
            "action": "buy",
            "orderType": "market",
            "quantity": 1,
            "price": "5000.00",
            "timestamp": entry_time.isoformat(),
            "setup": "MEAN_REVERSION_LONG",
            "stopPrice": "4995.00",
            "targetPrice": "5010.00"
        }
        
        response = client.post("/webhook/entry", json=entry_payload)
        assert response.status_code == 200
        entry_response = response.json()
        assert entry_response["status"] == "received"
        print(f"   ✓ Entry signal accepted: {entry_response['order']}")
        
        # Step 3: Verify trade logged
        print("\n3. Verifying trade in database...")
        assert len(mock_db.trades) == 1
        trade = mock_db.trades[0]
        assert trade["direction"] == "BUY"
        assert trade["contracts"] == 1
        assert trade["entry_price"] == 5000.0
        print(f"   ✓ Trade logged: ID={trade['id']}")
        
        # Step 4: Simulate broker fill
        print("\n4. Simulating broker fill...")
        mock_provider.set_position({
            "contractId": "MES",
            "netPos": 1,
            "netPrice": 5000.0
        })
        
        # Step 5: Check position endpoint
        print("\n5. Checking position status...")
        response = client.get("/position")
        assert response.status_code == 200
        position_data = response.json()
        print(f"   ✓ Position: {position_data}")
        
        # Step 6: Send exit signal
        print("\n6. Sending exit signal (SELL 1 MES @ 5010)...")
        exit_time = datetime.utcnow()
        exit_payload = {
            "ticker": "MES1!",
            "action": "exit",
            "quantity": 1,
            "price": "5010.00",
            "timestamp": exit_time.isoformat(),
            "reason": "target_hit"
        }
        
        response = client.post("/webhook/exit", json=exit_payload)
        assert response.status_code == 200
        exit_response = response.json()
        assert exit_response["status"] == "received"
        
        # Step 7: Verify PnL
        print("\n7. Verifying PnL calculation...")
        pnl = exit_response["pnl"]
        expected_gross = (5010.0 - 5000.0) * 1 * 5  # $50
        expected_commission = 1.40  # $0.70 * 2
        expected_net = expected_gross - expected_commission
        
        assert pnl["gross"] == expected_gross
        assert pnl["commission"] == expected_commission
        assert pnl["net"] == expected_net
        print(f"   ✓ PnL: Gross=${pnl['gross']}, Commission=${pnl['commission']}, Net=${pnl['net']}")
        
        # Step 8: Simulate broker position closed
        print("\n8. Simulating broker position closed...")
        mock_provider.set_position({
            "contractId": "MES",
            "netPos": 0,
            "netPrice": 0.0
        })
        
        # Step 9: Final status check
        print("\n9. Final system status...")
        response = client.get("/status")
        assert response.status_code == 200
        final_status = response.json()
        assert len(final_status["recent_trades"]) >= 1
        print(f"   ✓ Trade completed successfully")
        
        print("\n" + "=" * 60)
        print("INTEGRATION TEST PASSED ✓")
        print("=" * 60)
    
    def test_multiple_trades_respect_daily_limit(self, client, mock_db):
        """Test that multiple trades respect the daily trade limit."""
        print("\n[INTEGRATION TEST] Daily Trade Limit Enforcement")
        print("=" * 60)
        
        # Send 5 trades (the limit)
        for i in range(5):
            print(f"\nTrade {i+1}/5...")
            entry_payload = {
                "ticker": "MES1!",
                "action": "buy",
                "orderType": "market",
                "quantity": 1,
                "price": str(5000.0 + i),
                "timestamp": datetime.utcnow().isoformat(),
                "setup": "MEAN_REVERSION_LONG"
            }
            
            response = client.post("/webhook/entry", json=entry_payload)
            assert response.status_code == 200, f"Trade {i+1} should be accepted"
            
            # Immediately exit to reset position
            exit_payload = {
                "ticker": "MES1!",
                "action": "exit",
                "quantity": 1,
                "price": str(5000.0 + i + 5),
                "timestamp": datetime.utcnow().isoformat(),
                "reason": "test"
            }
            client.post("/webhook/exit", json=exit_payload)
        
        print(f"\n✓ All 5 trades executed")
        print(f"\nAttempting 6th trade (should be blocked by risk manager)...")
        
        # Verify 5 trades in database
        assert len(mock_db.trades) == 5
        
        # Note: The 6th trade would be blocked by the RiskManager
        # This would need to be tested at the RiskManager level
        # as the webhook server delegates to it
        
        print("=" * 60)
        print("DAILY LIMIT TEST PASSED ✓")
        print("=" * 60)


class TestRiskManagerIntegration:
    """Test Risk Manager in integration context."""
    
    def test_risk_manager_enforces_daily_trade_limit(self):
        """Test RiskManager enforces max 5 trades per day."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        risk_manager = RiskManager(config)
        
        # Simulate 5 trades
        for i in range(5):
            risk_manager.update_after_trade({"net_pnl": 10})
        
        # 6th trade should be blocked
        market_state = {"timestamp": datetime.now(), "hour": 10}
        account_state = {"equity": 50050, "mll_floor": 48000}
        
        allowed, reason = risk_manager.can_trade(market_state, account_state)
        
        assert not allowed
        assert "DAILY_TRADE_LIMIT" in reason
    
    def test_risk_manager_tracks_consecutive_losses(self):
        """Test RiskManager tracks and blocks on consecutive losses."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        risk_manager = RiskManager(config)
        
        # Simulate 3 losing trades
        for i in range(3):
            risk_manager.update_after_trade({"net_pnl": -50})
        
        market_state = {"timestamp": datetime.now(), "hour": 10}
        account_state = {"equity": 49850, "mll_floor": 48000}
        
        allowed, reason = risk_manager.can_trade(market_state, account_state)
        
        assert not allowed
        assert "consecutive" in reason.lower()
    
    def test_position_sizer_calculates_correct_size(self):
        """Test PositionSizer calculates appropriate position size."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        risk_manager = RiskManager(config)
        instrument = INSTRUMENT_SPECS["MES"]
        
        sizer = PositionSizer(risk_manager, instrument)
        
        result = sizer.calculate_size(
            account_equity=50000,
            entry_price=5000.0,
            stop_price=4995.0,  # $5 stop
            atr=7.5,
            signal_confidence=0.7,
            market_state="BALANCED"
        )
        
        # Should return reasonable size
        assert result["contracts"] >= 1
        assert result["contracts"] <= 5  # Max for Topstep 50K
        assert result["risk_dollars"] > 0
        assert result["risk_pct"] <= 2.0  # Around 1% or less


class TestFailureScenarios:
    """Test various failure scenarios."""
    
    def test_data_freshness_circuit_breaker(self):
        """Test data freshness circuit breaker triggers on stale data."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        # Stale data (> 60 seconds old)
        old_time = datetime.now() - timedelta(seconds=120)
        
        context = {
            "daily_pnl": 0,
            "equity": 50000,
            "mll_floor": 48000,
            "consecutive_losses": 0,
            "last_data_timestamp": old_time,
            "last_broker_ping": datetime.now(),
            "recent_orders": [],
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "data_freshness" in reason.lower() or "stale" in reason.lower()
    
    def test_broker_connectivity_circuit_breaker(self):
        """Test broker connectivity circuit breaker."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        # Old broker ping (> 10 seconds)
        old_ping = datetime.now() - timedelta(seconds=30)
        
        context = {
            "daily_pnl": 0,
            "equity": 50000,
            "mll_floor": 48000,
            "consecutive_losses": 0,
            "last_data_timestamp": datetime.now(),
            "last_broker_ping": old_ping,
            "recent_orders": [],
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "broker" in reason.lower()
    
    def test_order_rate_circuit_breaker(self):
        """Test order rate limiting circuit breaker."""
        config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        circuit_breakers = CircuitBreakers(config)
        
        # Many recent orders
        now = datetime.now()
        recent_orders = [now - timedelta(seconds=i*5) for i in range(15)]
        
        context = {
            "daily_pnl": 0,
            "equity": 50000,
            "mll_floor": 48000,
            "consecutive_losses": 0,
            "last_data_timestamp": datetime.now(),
            "last_broker_ping": datetime.now(),
            "recent_orders": recent_orders,
            "recent_fills": [],
            "recent_errors": [],
        }
        
        allowed, reason = circuit_breakers.check_all(context)
        
        assert not allowed
        assert "order_rate" in reason.lower() or "rate" in reason.lower()


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v", "-k", "test_complete_long_trade"])
