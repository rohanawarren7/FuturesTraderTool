"""
Interactive Brokers Position Synchronization
Replaces Tradovate position sync for IBKR TWS (FREE)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Optional, Dict, Any
from datetime import datetime
import logging

from data.ibkr_provider import IBKRDataProvider
from database.db_manager import DBManager

logger = logging.getLogger(__name__)


class IBKRPositionSynchronizer:
    """
    Synchronizes positions between IBKR TWS and local database.
    """
    
    def __init__(self, provider: IBKRDataProvider, db: DBManager):
        self.provider = provider
        self.db = db
        self.last_sync_time: Optional[datetime] = None
        
    def sync_on_startup(self) -> Dict[str, Any]:
        """
        Synchronize positions on bot startup.
        
        Returns:
            Sync result with status and discrepancies
        """
        logger.info("[IBKR-Sync] Starting position synchronization...")
        
        try:
            # Ensure connected
            if not self.provider.is_connected():
                if not self.provider.connect():
                    return {
                        "status": "ERROR",
                        "error": "Failed to connect to IBKR"
                    }
            
            # Get broker positions
            broker_positions = self.provider.get_positions()
            
            # Get local positions (from DB)
            recent_trades = self.db.get_recent_live_trades(limit=10)
            local_positions = self._extract_local_positions(recent_trades)
            
            # Detect discrepancies
            discrepancies = self._detect_discrepancies(broker_positions, local_positions)
            
            result = {
                "status": "SYNCED" if not discrepancies else "RECONCILED",
                "broker_positions": broker_positions,
                "local_positions": local_positions,
                "discrepancies": discrepancies,
                "timestamp": datetime.now().isoformat()
            }
            
            self.last_sync_time = datetime.now()
            
            logger.info(f"[IBKR-Sync] Sync complete: {result['status']}")
            return result
            
        except Exception as e:
            logger.error(f"[IBKR-Sync] Sync error: {e}")
            return {
                "status": "ERROR",
                "error": str(e)
            }
    
    def _extract_local_positions(self, trades: list) -> list:
        """Extract open positions from recent trades."""
        positions = []
        
        for trade in trades:
            if trade.get("exit_time") is None:
                # This is an open position
                positions.append({
                    "symbol": trade.get("instrument"),
                    "quantity": trade.get("contracts", 0) * (1 if trade.get("direction") == "BUY" else -1),
                    "avg_cost": trade.get("entry_price", 0)
                })
        
        return positions
    
    def _detect_discrepancies(self, broker_positions: list, local_positions: list) -> list:
        """Detect differences between broker and local state."""
        discrepancies = []
        
        # Create lookup dicts
        broker_by_symbol = {p['symbol']: p for p in broker_positions}
        local_by_symbol = {p['symbol']: p for p in local_positions}
        
        all_symbols = set(broker_by_symbol.keys()) | set(local_by_symbol.keys())
        
        for symbol in all_symbols:
            broker_pos = broker_by_symbol.get(symbol)
            local_pos = local_by_symbol.get(symbol)
            
            if broker_pos and not local_pos:
                discrepancies.append({
                    "type": "MISSING_LOCAL_POSITION",
                    "symbol": symbol,
                    "broker_qty": broker_pos['quantity'],
                    "local_qty": 0
                })
            elif local_pos and not broker_pos:
                discrepancies.append({
                    "type": "MISSING_BROKER_POSITION",
                    "symbol": symbol,
                    "broker_qty": 0,
                    "local_qty": local_pos['quantity']
                })
            elif broker_pos and local_pos:
                if abs(broker_pos['quantity'] - local_pos['quantity']) > 0.01:
                    discrepancies.append({
                        "type": "QUANTITY_MISMATCH",
                        "symbol": symbol,
                        "broker_qty": broker_pos['quantity'],
                        "local_qty": local_pos['quantity']
                    })
        
        return discrepancies
    
    def emergency_flatten(self, reason: str) -> Dict[str, Any]:
        """
        Emergency close all positions.
        
        Args:
            reason: Why the emergency flatten was triggered
            
        Returns:
            Result of flatten operation
        """
        logger.info(f"[IBKR-Sync] EMERGENCY FLATTEN: {reason}")
        
        result = {
            "success": False,
            "reason": reason,
            "orders": [],
            "errors": []
        }
        
        try:
            if not self.provider.is_connected():
                if not self.provider.connect():
                    result["errors"].append("Not connected to IBKR")
                    return result
            
            # Flatten all positions
            success = self.provider.flatten_all_positions()
            
            if success:
                result["success"] = True
                logger.info("[IBKR-Sync] Emergency flatten successful")
            else:
                result["errors"].append("Failed to flatten positions")
                
        except Exception as e:
            logger.error(f"[IBKR-Sync] Emergency flatten error: {e}")
            result["errors"].append(str(e))
        
        return result
    
    def get_position_status(self) -> Dict[str, Any]:
        """Get current position status."""
        try:
            if not self.provider.is_connected():
                self.provider.connect()
            
            broker_positions = self.provider.get_positions()
            account = self.provider.get_account_summary()
            
            return {
                "connected": self.provider.is_connected(),
                "positions": broker_positions,
                "account": account,
                "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None
            }
            
        except Exception as e:
            logger.error(f"[IBKR-Sync] Error getting position status: {e}")
            return {
                "connected": False,
                "error": str(e)
            }
    
    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        try:
            if not self.provider.is_connected():
                self.provider.connect()
            
            return self.provider.cancel_all_orders()
            
        except Exception as e:
            logger.error(f"[IBKR-Sync] Error cancelling orders: {e}")
            return False
