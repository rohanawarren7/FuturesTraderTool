"""
Interactive Brokers (IBKR) Data and Trading Provider
Free alternative to Tradovate - no monthly API fees
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import time
import logging

# IBKR imports
try:
    from ib_insync import IB, Future, Stock, MarketOrder, LimitOrder, StopOrder
    from ib_insync import util as ib_util
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    print("[IBKR] Warning: ib_insync not installed. Run: pip install ib_insync")


logger = logging.getLogger(__name__)


class IBKRDataProvider:
    """
    Interactive Brokers data provider and trade executor.
    
    Requires:
    - TWS (Trader Workstation) or IB Gateway running
    - API connections enabled in TWS settings
    
    Paper Trading Port: 7497
    Live Trading Port: 7496
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        timeout: int = 10
    ):
        """
        Initialize IBKR provider.
        
        Args:
            host: TWS host (default: localhost)
            port: TWS port (7497=paper, 7496=live)
            client_id: Unique client ID (default: 1)
            timeout: Connection timeout in seconds
        """
        if not IBKR_AVAILABLE:
            raise ImportError("ib_insync not installed. Run: pip install ib_insync")
        
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.ib = IB()
        self._connected = False
        
        # Contract cache
        self._contract_cache: Dict[str, Any] = {}
        
    def connect(self) -> bool:
        """
        Connect to TWS/IB Gateway.
        
        Returns:
            True if connected successfully
        """
        try:
            if self.ib.isConnected():
                logger.info("[IBKR] Already connected")
                self._connected = True
                return True
            
            logger.info(f"[IBKR] Connecting to {self.host}:{self.port}...")
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
            
            if self.ib.isConnected():
                self._connected = True
                account = self.ib.managedAccounts()[0] if self.ib.managedAccounts() else "Unknown"
                logger.info(f"[IBKR] Connected! Account: {account}")
                return True
            else:
                logger.error("[IBKR] Connection failed")
                return False
                
        except Exception as e:
            logger.error(f"[IBKR] Connection error: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Disconnect from TWS."""
        if self.ib.isConnected():
            self.ib.disconnect()
            self._connected = False
            logger.info("[IBKR] Disconnected")
    
    def is_connected(self) -> bool:
        """Check if connected to TWS."""
        return self.ib.isConnected()
    
    def get_contract(self, symbol: str, expiry: Optional[str] = None) -> Optional[Future]:
        """
        Get futures contract for symbol.
        
        Args:
            symbol: Futures symbol (MES, MNQ, ES, etc.)
            expiry: Expiry code (e.g., '202403' or 'H24')
                 If None, uses front month
                 
        Returns:
            Qualified contract or None
        """
        cache_key = f"{symbol}_{expiry}"
        
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        
        try:
            if expiry is None:
                expiry = self._get_front_month(symbol)
            
            # Create contract
            contract = Future(symbol, expiry, 'GLOBEX')
            
            # Qualify contract (resolve conId, etc.)
            qualified = self.ib.qualifyContracts(contract)
            
            if qualified:
                self._contract_cache[cache_key] = qualified[0]
                return qualified[0]
            else:
                logger.error(f"[IBKR] Could not qualify contract: {symbol} {expiry}")
                return None
                
        except Exception as e:
            logger.error(f"[IBKR] Error getting contract: {e}")
            return None
    
    def get_historical_data(
        self,
        symbol: str,
        duration: str = "1 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Get historical OHLCV data.
        
        Args:
            symbol: Futures symbol
            duration: Duration string (e.g., "1 D", "5 D", "1 Y")
            bar_size: Bar size (e.g., "1 min", "5 mins", "1 hour")
            what_to_show: Data type ("TRADES", "MIDPOINT", etc.)
            use_rth: Use regular trading hours only
            
        Returns:
            DataFrame with OHLCV data
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return pd.DataFrame()
        
        try:
            contract = self.get_contract(symbol)
            if not contract:
                return pd.DataFrame()
            
            logger.info(f"[IBKR] Requesting {duration} of {bar_size} data for {symbol}")
            
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1
            )
            
            if not bars:
                logger.warning(f"[IBKR] No data returned for {symbol}")
                return pd.DataFrame()
            
            # Convert to DataFrame
            df = ib_util.df(bars)
            
            if df is not None and not df.empty:
                # Standardize column names
                df.rename(columns={
                    'date': 'timestamp',
                    'open': 'open',
                    'high': 'high',
                    'low': 'low',
                    'close': 'close',
                    'volume': 'volume',
                    'average': 'vwap',
                    'barCount': 'bar_count'
                }, inplace=True, errors='ignore')
                
                # Ensure timestamp is datetime
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                
                logger.info(f"[IBKR] Received {len(df)} bars")
                return df
            else:
                return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"[IBKR] Error fetching historical data: {e}")
            return pd.DataFrame()
    
    def get_realtime_bars(
        self,
        symbol: str,
        callback: Optional[callable] = None
    ) -> Any:
        """
        Subscribe to real-time bars.
        
        Args:
            symbol: Futures symbol
            callback: Function to call with each new bar
            
        Returns:
            Bar update event
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return None
        
        try:
            contract = self.get_contract(symbol)
            if not contract:
                return None
            
            # Request real-time bars (5-second bars)
            bars = self.ib.reqRealTimeBars(contract, 5, 'TRADES', False)
            
            if callback:
                bars.updateEvent += callback
            
            logger.info(f"[IBKR] Subscribed to real-time bars for {symbol}")
            return bars
            
        except Exception as e:
            logger.error(f"[IBKR] Error subscribing to real-time bars: {e}")
            return None
    
    def place_market_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        account: Optional[str] = None
    ) -> Optional[Any]:
        """
        Place market order.
        
        Args:
            symbol: Futures symbol
            action: 'BUY' or 'SELL'
            quantity: Number of contracts
            account: Account ID (optional)
            
        Returns:
            Trade object or None
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return None
        
        try:
            contract = self.get_contract(symbol)
            if not contract:
                return None
            
            order = MarketOrder(action, quantity)
            
            if account:
                order.account = account
            
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(f"[IBKR] Market order placed: {action} {quantity} {symbol}")
            logger.info(f"[IBKR] Order ID: {trade.order.orderId}")
            
            return trade
            
        except Exception as e:
            logger.error(f"[IBKR] Error placing market order: {e}")
            return None
    
    def place_limit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
        account: Optional[str] = None
    ) -> Optional[Any]:
        """
        Place limit order.
        
        Args:
            symbol: Futures symbol
            action: 'BUY' or 'SELL'
            quantity: Number of contracts
            limit_price: Limit price
            account: Account ID (optional)
            
        Returns:
            Trade object or None
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return None
        
        try:
            contract = self.get_contract(symbol)
            if not contract:
                return None
            
            order = LimitOrder(action, quantity, limit_price)
            
            if account:
                order.account = account
            
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(f"[IBKR] Limit order placed: {action} {quantity} {symbol} @ {limit_price}")
            
            return trade
            
        except Exception as e:
            logger.error(f"[IBKR] Error placing limit order: {e}")
            return None
    
    def place_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        target_price: float,
        account: Optional[str] = None
    ) -> List[Any]:
        """
        Place bracket order with entry, stop, and target.
        
        Args:
            symbol: Futures symbol
            action: 'BUY' or 'SELL'
            quantity: Number of contracts
            entry_price: Entry limit price
            stop_price: Stop loss price
            target_price: Profit target price
            account: Account ID (optional)
            
        Returns:
            List of trade objects
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return []
        
        try:
            contract = self.get_contract(symbol)
            if not contract:
                return []
            
            # Create bracket order
            parent = LimitOrder(action, quantity, entry_price)
            parent.transmit = False
            
            if account:
                parent.account = account
            
            # Stop loss
            stop_action = 'SELL' if action == 'BUY' else 'BUY'
            stop_loss = StopOrder(stop_action, quantity, stop_price)
            stop_loss.transmit = False
            
            # Take profit
            take_profit = LimitOrder(stop_action, quantity, target_price)
            take_profit.transmit = True  # Last order transmits
            
            # Link orders
            stop_loss.parentId = parent.orderId
            take_profit.parentId = parent.orderId
            
            # Place orders
            parent_trade = self.ib.placeOrder(contract, parent)
            stop_trade = self.ib.placeOrder(contract, stop_loss)
            target_trade = self.ib.placeOrder(contract, take_profit)
            
            logger.info(f"[IBKR] Bracket order placed: {action} {quantity} {symbol}")
            logger.info(f"[IBKR] Entry: {entry_price}, Stop: {stop_price}, Target: {target_price}")
            
            return [parent_trade, stop_trade, target_trade]
            
        except Exception as e:
            logger.error(f"[IBKR] Error placing bracket order: {e}")
            return []
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current positions.
        
        Returns:
            List of position dictionaries
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return []
        
        try:
            positions = self.ib.positions()
            
            result = []
            for pos in positions:
                result.append({
                    'account': pos.account,
                    'symbol': pos.contract.symbol if hasattr(pos.contract, 'symbol') else 'Unknown',
                    'expiry': pos.contract.lastTradeDateOrContractMonth if hasattr(pos.contract, 'lastTradeDateOrContractMonth') else '',
                    'quantity': pos.position,
                    'avg_cost': pos.avgCost,
                    'market_price': pos.marketPrice if hasattr(pos, 'marketPrice') else 0,
                    'market_value': pos.marketValue if hasattr(pos, 'marketValue') else 0,
                    'unrealized_pnl': pos.unrealizedPNL if hasattr(pos, 'unrealizedPNL') else 0,
                    'realized_pnl': pos.realizedPNL if hasattr(pos, 'realizedPNL') else 0,
                })
            
            return result
            
        except Exception as e:
            logger.error(f"[IBKR] Error getting positions: {e}")
            return []
    
    def get_account_summary(self) -> Dict[str, Any]:
        """
        Get account summary.
        
        Returns:
            Dictionary with account information
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return {}
        
        try:
            account = self.ib.managedAccounts()[0] if self.ib.managedAccounts() else None
            
            if not account:
                return {}
            
            # Request account values
            values = self.ib.accountValues(account)
            
            summary = {}
            for v in values:
                if v.tag in ['NetLiquidation', 'AvailableFunds', 'BuyingPower', 
                            'UnrealizedPnL', 'RealizedPnL', 'MaintMarginReq']:
                    summary[v.tag] = float(v.value) if v.value else 0
            
            summary['account'] = account
            summary['timestamp'] = datetime.now().isoformat()
            
            return summary
            
        except Exception as e:
            logger.error(f"[IBKR] Error getting account summary: {e}")
            return {}
    
    def cancel_all_orders(self) -> bool:
        """
        Cancel all open orders.
        
        Returns:
            True if successful
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return False
        
        try:
            self.ib.reqGlobalCancel()
            logger.info("[IBKR] All orders cancelled")
            return True
            
        except Exception as e:
            logger.error(f"[IBKR] Error cancelling orders: {e}")
            return False
    
    def flatten_all_positions(self) -> bool:
        """
        Close all positions (emergency flatten).
        
        Returns:
            True if successful
        """
        if not self.is_connected():
            logger.error("[IBKR] Not connected")
            return False
        
        try:
            positions = self.get_positions()
            
            for pos in positions:
                if pos['quantity'] != 0:
                    action = 'SELL' if pos['quantity'] > 0 else 'BUY'
                    quantity = abs(pos['quantity'])
                    symbol = pos['symbol']
                    
                    self.place_market_order(symbol, action, quantity)
                    logger.info(f"[IBKR] Flattened {quantity} {symbol}")
            
            logger.info("[IBKR] All positions flattened")
            return True
            
        except Exception as e:
            logger.error(f"[IBKR] Error flattening positions: {e}")
            return False
    
    def _get_front_month(self, symbol: str) -> str:
        """
        Calculate front month expiry for futures.
        
        Args:
            symbol: Futures symbol
            
        Returns:
            Expiry code (e.g., '202403' or 'H24')
        """
        now = datetime.now()
        
        # Quarterly months: March(H), June(M), September(U), December(Z)
        month_codes = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}
        quarterly_months = [3, 6, 9, 12]
        
        # Find next quarterly month
        target_month = None
        for month in quarterly_months:
            if now.month < month or (now.month == month and now.day < 15):
                target_month = month
                break
        
        # If after Dec 15, roll to March next year
        if target_month is None:
            target_month = 3
            year = now.year + 1
        else:
            year = now.year
        
        # Format: YYYYMM (e.g., 202403) or H24 (short form)
        # IBKR accepts both, let's use YYYYMM format
        return f"{year}{target_month:02d}"
    
    @staticmethod
    def from_env() -> 'IBKRDataProvider':
        """
        Create IBKR provider from environment variables.
        
        Required env vars:
            IBKR_HOST (default: 127.0.0.1)
            IBKR_PORT (default: 7497)
            IBKR_CLIENT_ID (default: 1)
        """
        import os
        
        host = os.getenv('IBKR_HOST', '127.0.0.1')
        port = int(os.getenv('IBKR_PORT', '7497'))
        client_id = int(os.getenv('IBKR_CLIENT_ID', '1'))
        
        return IBKRDataProvider(host=host, port=port, client_id=client_id)


# Example usage
if __name__ == "__main__":
    # Test connection
    provider = IBKRDataProvider()
    
    if provider.connect():
        print("✓ Connected to IBKR")
        
        # Get account info
        account = provider.get_account_summary()
        print(f"Account: {account}")
        
        # Get positions
        positions = provider.get_positions()
        print(f"Positions: {positions}")
        
        # Get historical data
        df = provider.get_historical_data('MES', duration='1 D', bar_size='5 mins')
        print(f"Data: {len(df)} bars")
        print(df.head())
        
        provider.disconnect()
    else:
        print("✗ Failed to connect")
        print("Make sure TWS is running with API enabled on port 7497")
