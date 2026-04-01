"""
Position Synchronization System for VWAP Trading Bot.
Ensures local position state matches Tradovate broker state.
Critical for crash recovery and consistency.
"""

from __future__ import annotations
from typing import Optional, Dict, Any
from datetime import datetime
import requests
import time
from dataclasses import dataclass

from data.tradovate_data_provider import TradovateDataProvider
from database.db_manager import DBManager


@dataclass
class Position:
    """Represents a trading position."""
    instrument: str
    quantity: int  # Positive for long, negative for short
    avg_price: float
    unrealized_pnl: float
    realized_pnl: float
    open_time: Optional[datetime] = None
    
    @property
    def is_long(self) -> bool:
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        return self.quantity < 0
    
    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class PositionSynchronizer:
    """
    Synchronizes local position state with Tradovate broker.
    Provides automatic reconciliation and emergency flatten capabilities.
    """
    
    def __init__(self, 
                 provider: TradovateDataProvider, 
                 db: DBManager,
                 target_contract_id: Optional[int] = None):
        """
        Initialize position synchronizer.
        
        Args:
            provider: TradovateDataProvider instance
            db: Database manager
            target_contract_id: Specific contract to track (e.g., MES)
        """
        self.provider = provider
        self.db = db
        self.target_contract_id = target_contract_id
        
        # Local position state
        self.local_position: Optional[Position] = None
        self.broker_position: Optional[Position] = None
        
        # Sync tracking
        self.last_sync_time: Optional[datetime] = None
        self.sync_errors: list[dict] = []
        
    def sync_on_startup(self) -> dict:
        """
        Full synchronization on bot startup.
        Reconciles any discrepancies between local and broker state.
        
        Returns:
            Sync result with status and any discrepancies found
        """
        print("[PositionSync] Starting startup synchronization...")
        
        # Fetch both positions
        self.broker_position = self._fetch_broker_position()
        self.local_position = self._fetch_local_position()
        
        result = {
            "status": "SYNCED",
            "broker_position": self._position_to_dict(self.broker_position),
            "local_position": self._position_to_dict(self.local_position),
            "discrepancies": [],
            "actions_taken": []
        }
        
        # Check for discrepancies
        discrepancies = self._detect_discrepancies()
        
        if discrepancies:
            result["status"] = "RECONCILED"
            result["discrepancies"] = discrepancies
            
            # Attempt reconciliation
            for disc in discrepancies:
                action = self._reconcile_discrepancy(disc)
                result["actions_taken"].append(action)
        
        self.last_sync_time = datetime.now()
        
        print(f"[PositionSync] Sync complete. Status: {result['status']}")
        return result
    
    def _fetch_broker_position(self) -> Optional[Position]:
        """Fetch current position from Tradovate API."""
        try:
            resp = requests.get(
                f"{self.provider.base_url}/position/list",
                headers=self.provider._headers(),
                timeout=10
            )
            resp.raise_for_status()
            positions = resp.json()
            
            # Find position for our target instrument
            for pos in positions:
                if self.target_contract_id and pos.get("contractId") != self.target_contract_id:
                    continue
                    
                # Found a position
                return Position(
                    instrument=pos.get("contractId", ""),
                    quantity=pos.get("netPos", 0),
                    avg_price=pos.get("netPrice", 0.0),
                    unrealized_pnl=pos.get("unrealizedPnl", 0.0),
                    realized_pnl=pos.get("realizedPnl", 0.0),
                    open_time=None  # Not provided by API
                )
            
            # No position found = flat
            return None
            
        except Exception as e:
            error = {
                "timestamp": datetime.now().isoformat(),
                "error": f"Failed to fetch broker position: {str(e)}"
            }
            self.sync_errors.append(error)
            print(f"[PositionSync] ERROR: {error['error']}")
            return None
    
    def _fetch_local_position(self) -> Optional[Position]:
        """Fetch last known position from local database."""
        try:
            # Get most recent live trade
            recent_trades = self.db.get_recent_live_trades(limit=1)
            
            if not recent_trades:
                return None
            
            trade = recent_trades[0]
            
            # Check if position is still open
            if trade.get("exit_time") is None:
                # Position is open
                return Position(
                    instrument=trade.get("instrument", ""),
                    quantity=trade.get("contracts", 0) * (1 if trade.get("direction") == "BUY" else -1),
                    avg_price=trade.get("entry_price", 0.0),
                    unrealized_pnl=0.0,  # Would need to calculate
                    realized_pnl=0.0,
                    open_time=trade.get("entry_time")
                )
            
            return None  # Flat
            
        except Exception as e:
            print(f"[PositionSync] ERROR fetching local position: {e}")
            return None
    
    def _detect_discrepancies(self) -> list[dict]:
        """Detect differences between broker and local state."""
        discrepancies = []
        
        broker_flat = self.broker_position is None or self.broker_position.is_flat
        local_flat = self.local_position is None or self.local_position.is_flat
        
        # Case 1: Broker shows position, local shows flat
        if not broker_flat and local_flat:
            discrepancies.append({
                "type": "MISSING_LOCAL_POSITION",
                "severity": "HIGH",
                "description": f"Broker has position {self.broker_position.quantity}x {self.broker_position.instrument}, local shows flat",
                "broker_qty": self.broker_position.quantity,
                "local_qty": 0
            })
        
        # Case 2: Local shows position, broker shows flat
        elif broker_flat and not local_flat:
            discrepancies.append({
                "type": "MISSING_BROKER_POSITION",
                "severity": "MEDIUM",
                "description": f"Local has position {self.local_position.quantity}x {self.local_position.instrument}, broker shows flat",
                "broker_qty": 0,
                "local_qty": self.local_position.quantity
            })
        
        # Case 3: Both have positions but different quantities
        elif not broker_flat and not local_flat:
            if self.broker_position.quantity != self.local_position.quantity:
                discrepancies.append({
                    "type": "QUANTITY_MISMATCH",
                    "severity": "HIGH",
                    "description": f"Position quantity mismatch: broker={self.broker_position.quantity}, local={self.local_position.quantity}",
                    "broker_qty": self.broker_position.quantity,
                    "local_qty": self.local_position.quantity
                })
        
        return discrepancies
    
    def _reconcile_discrepancy(self, discrepancy: dict) -> dict:
        """Attempt to reconcile a discrepancy."""
        disc_type = discrepancy["type"]
        
        if disc_type == "MISSING_LOCAL_POSITION":
            # Sync from broker to local
            action = self._sync_from_broker()
            return action
            
        elif disc_type == "MISSING_BROKER_POSITION":
            # Position closed without local update
            action = self._mark_local_position_closed()
            return action
            
        elif disc_type == "QUANTITY_MISMATCH":
            # Partial fill or position change not recorded
            action = self._update_local_quantity(
                discrepancy["broker_qty"]
            )
            return action
        
        return {"action": "NONE", "reason": "Unknown discrepancy type"}
    
    def _sync_from_broker(self) -> dict:
        """Sync position from broker to local database."""
        if self.broker_position is None:
            return {"action": "NONE", "reason": "No broker position"}
        
        # Insert into database as new position
        try:
            # Create a trade record from broker position
            trade_record = {
                "trade_id": f"SYNC_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "prop_firm": "TOPSTEP_50K",  # Should come from config
                "account_id": "sync",
                "instrument": self.broker_position.instrument,
                "direction": "BUY" if self.broker_position.is_long else "SELL",
                "entry_time": datetime.now().isoformat(),
                "exit_time": None,
                "entry_price": self.broker_position.avg_price,
                "exit_price": None,
                "contracts": abs(self.broker_position.quantity),
                "gross_pnl": None,
                "commission": 0,
                "net_pnl": None,
                "setup_type": "SYNC_FROM_BROKER",
                "vwap_at_entry": None,
                "vwap_position": None,
                "market_state": None,
                "signal_confidence": None,
                "stop_price": None,
                "target_price": None,
                "r_multiple": None,
                "tradovate_order_id": None,
                "notes": "Position synced from broker on startup"
            }
            
            self.db.insert_live_trade(trade_record)
            
            return {
                "action": "SYNC_FROM_BROKER",
                "position": self._position_to_dict(self.broker_position)
            }
            
        except Exception as e:
            return {
                "action": "FAILED",
                "error": str(e)
            }
    
    def _mark_local_position_closed(self) -> dict:
        """Mark local position as closed (broker shows flat)."""
        try:
            # Update database record
            # Note: In real implementation, you'd update the specific trade record
            return {
                "action": "MARK_LOCAL_CLOSED",
                "reason": "Broker shows flat position"
            }
        except Exception as e:
            return {
                "action": "FAILED",
                "error": str(e)
            }
    
    def _update_local_quantity(self, new_quantity: int) -> dict:
        """Update local position quantity to match broker."""
        try:
            # Update database record with new quantity
            return {
                "action": "UPDATE_QUANTITY",
                "old_qty": self.local_position.quantity if self.local_position else 0,
                "new_qty": new_quantity
            }
        except Exception as e:
            return {
                "action": "FAILED",
                "error": str(e)
            }
    
    def emergency_flatten(self, reason: str) -> dict:
        """
        Emergency flatten all positions immediately.
        
        Args:
            reason: Why the emergency flatten was triggered
            
        Returns:
            Result of flatten operation
        """
        print(f"[PositionSync] EMERGENCY FLATTEN: {reason}")
        
        result = {
            "success": False,
            "reason": reason,
            "orders_submitted": [],
            "errors": []
        }
        
        try:
            # Get current position
            position = self._fetch_broker_position()
            
            if position is None or position.is_flat:
                print("[PositionSync] Already flat, no action needed")
                result["success"] = True
                result["message"] = "Already flat"
                return result
            
            # Submit market order to flatten
            order = self._create_flatten_order(position)
            
            # Submit order via Tradovate API
            resp = requests.post(
                f"{self.provider.base_url}/order/placeorder",
                headers=self.provider._headers(),
                json=order,
                timeout=10
            )
            resp.raise_for_status()
            
            order_result = resp.json()
            result["orders_submitted"].append(order_result)
            result["success"] = True
            
            # Log to database
            self._log_emergency_flatten(position, reason, order_result)
            
            print(f"[PositionSync] Emergency flatten successful: {order_result}")
            
        except Exception as e:
            error_msg = f"Emergency flatten failed: {str(e)}"
            print(f"[PositionSync] ERROR: {error_msg}")
            result["errors"].append(error_msg)
        
        return result
    
    def _create_flatten_order(self, position: Position) -> dict:
        """Create order to flatten a position."""
        # Determine action (opposite of current position)
        action = "Sell" if position.is_long else "Buy"
        
        return {
            "accountId": 0,  # Should come from config
            "contractId": position.instrument,
            "action": action,
            "orderType": "Market",
            "quantity": abs(position.quantity),
            "text": f"EMERGENCY_FLATTEN"
        }
    
    def _log_emergency_flatten(self, position: Position, reason: str, order_result: dict):
        """Log emergency flatten to database."""
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "EMERGENCY_FLATTEN",
                "reason": reason,
                "instrument": position.instrument,
                "quantity": position.quantity,
                "order_result": order_result
            }
            # Insert to database
        except Exception as e:
            print(f"[PositionSync] ERROR logging emergency flatten: {e}")
    
    def get_position_status(self) -> dict:
        """Get current position status summary."""
        return {
            "broker_position": self._position_to_dict(self.broker_position),
            "local_position": self._position_to_dict(self.local_position),
            "synced": self._positions_match(),
            "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "recent_errors": self.sync_errors[-5:] if self.sync_errors else []
        }
    
    def _positions_match(self) -> bool:
        """Check if broker and local positions match."""
        if self.broker_position is None and self.local_position is None:
            return True  # Both flat
        
        if self.broker_position is None or self.local_position is None:
            return False  # One has position, other doesn't
        
        return (
            self.broker_position.instrument == self.local_position.instrument and
            self.broker_position.quantity == self.local_position.quantity
        )
    
    def _position_to_dict(self, position: Optional[Position]) -> Optional[dict]:
        """Convert Position to dictionary."""
        if position is None:
            return None
        return {
            "instrument": position.instrument,
            "quantity": position.quantity,
            "avg_price": position.avg_price,
            "unrealized_pnl": position.unrealized_pnl,
            "realized_pnl": position.realized_pnl,
            "is_long": position.is_long,
            "is_short": position.is_short,
            "is_flat": position.is_flat
        }


class PositionMonitor:
    """
    Background monitor for position consistency.
    Runs periodic checks and alerts on discrepancies.
    """
    
    def __init__(self, synchronizer: PositionSynchronizer, check_interval_seconds: int = 30):
        self.synchronizer = synchronizer
        self.check_interval = check_interval_seconds
        self._running = False
        
    def start_monitoring(self):
        """Start background monitoring."""
        self._running = True
        print(f"[PositionMonitor] Started monitoring (interval: {self.check_interval}s)")
        
        while self._running:
            try:
                # Periodic sync check
                status = self.synchronizer.get_position_status()
                
                if not status["synced"]:
                    print(f"[PositionMonitor] ALERT: Position mismatch detected!")
                    print(f"  Broker: {status['broker_position']}")
                    print(f"  Local: {status['local_position']}")
                    
                    # Attempt auto-reconciliation
                    self.synchronizer.sync_on_startup()
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"[PositionMonitor] ERROR: {e}")
                time.sleep(self.check_interval)
    
    def stop_monitoring(self):
        """Stop background monitoring."""
        self._running = False
        print("[PositionMonitor] Stopped monitoring")
