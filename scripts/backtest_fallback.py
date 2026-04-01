#!/usr/bin/env python3
"""
Historical Backtest - Test Fallback Strategy on Real Data
===========================================================

Fetches historical data from IBKR and runs the fallback strategy through it.
Shows performance metrics without risking real money.

Usage:
    python scripts/backtest_fallback.py --symbol MES --days 30
    python scripts/backtest_fallback.py --symbol MES --start 2024-01-01 --end 2024-01-31
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging

from data.ibkr_provider import IBKRDataProvider
from core.signal_generator import SignalGenerator, FALLBACK_SETUPS
from core.risk_manager import RiskManager
from core.position_sizer import PositionSizer
from core.vwap_calculator import VWAPCalculator
from core.market_state_detector import MarketStateDetector
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HistoricalBacktest:
    """
    Backtest the fallback strategy on historical data.
    """
    
    def __init__(self, symbol: str = "MES", account_size: float = 50000):
        self.symbol = symbol
        self.account_size = account_size
        self.point_value = INSTRUMENT_SPECS[symbol]["point_value"]
        
        # Initialize components
        self.prop_config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        self.risk_manager = RiskManager(self.prop_config)
        self.position_sizer = PositionSizer(self.risk_manager, INSTRUMENT_SPECS[symbol])
        self.signal_gen = SignalGenerator(video_trade_count=0, use_fallback=True)
        self.vwap_calc = VWAPCalculator()
        self.market_detector = MarketStateDetector()
        
        # Results tracking
        self.trades: List[Dict] = []
        self.daily_stats: List[Dict] = []
        
    def fetch_data(self, duration: str = "30 D", bar_size: str = "5 mins") -> pd.DataFrame:
        """
        Fetch historical data from IBKR.
        
        Args:
            duration: How much data (e.g., "30 D", "3 M")
            bar_size: Bar size (e.g., "1 min", "5 mins", "1 hour")
            
        Returns:
            DataFrame with OHLCV data
        """
        logger.info(f"Fetching {duration} of {bar_size} data for {self.symbol}...")
        
        provider = IBKRDataProvider.from_env()
        
        if not provider.connect():
            logger.error("Failed to connect to IBKR. Make sure TWS is running.")
            return pd.DataFrame()
        
        try:
            df = provider.get_historical_data(
                symbol=self.symbol,
                duration=duration,
                bar_size=bar_size
            )
            
            provider.disconnect()
            
            if df.empty:
                logger.error("No data returned")
                return pd.DataFrame()
            
            logger.info(f"Retrieved {len(df)} bars")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            provider.disconnect()
            return pd.DataFrame()
    
    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add technical indicators needed for signals.
        """
        # Calculate VWAP
        df = self.vwap_calc.calculate_session_vwap(df)
        
        # Calculate ATR for volatility
        df['atr'] = self._calculate_atr(df)
        
        # Calculate volume ratio
        df['volume_avg'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_avg']
        
        # Delta proxy (close - open)
        df['delta'] = df['close'] - df['open']
        df['delta_direction'] = df['delta'].apply(
            lambda x: 'POSITIVE' if x > 0 else ('NEGATIVE' if x < 0 else 'NEUTRAL')
        )
        
        # Delta flip detection
        df['delta_flip'] = (
            (df['delta'] > 0) & (df['delta'].shift(1) <= 0) |
            (df['delta'] < 0) & (df['delta'].shift(1) >= 0)
        )
        
        # VWAP position
        df['vwap_position'] = df.apply(self._get_vwap_position, axis=1)
        
        # Market state
        df['market_state'] = df.apply(self._detect_market_state, axis=1)
        
        # Time in session (minutes from 9:30 ET)
        df['time_in_session'] = df['timestamp'].apply(self._time_in_session)
        
        return df
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))
        
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        
        return atr
    
    def _get_vwap_position(self, row) -> str:
        """Determine price position relative to VWAP bands."""
        close = row['close']
        vwap = row.get('vwap', close)
        sd1_upper = row.get('vwap_sd1_upper', vwap)
        sd1_lower = row.get('vwap_sd1_lower', vwap)
        sd2_upper = row.get('vwap_sd2_upper', vwap + (sd1_upper - vwap) * 2)
        sd2_lower = row.get('vwap_sd2_lower', vwap - (vwap - sd1_lower) * 2)
        
        if close > sd2_upper:
            return "ABOVE_SD2"
        elif close > sd1_upper:
            return "ABOVE_SD1"
        elif close < sd2_lower:
            return "BELOW_SD2"
        elif close < sd1_lower:
            return "BELOW_SD1"
        else:
            return "INSIDE_SD1"
    
    def _detect_market_state(self, row) -> str:
        """Detect market regime."""
        # Simplified logic - use ATR ratio and VWAP position
        atr_ratio = row.get('atr', 1) / row.get('atr', 1)  # Would need historical context
        
        # For now, use basic logic
        vwap_pos = row.get('vwap_position', 'INSIDE_SD1')
        volume_ratio = row.get('volume_ratio', 1)
        
        if vwap_pos in ['ABOVE_SD1', 'BELOW_SD1']:
            return "BALANCED"
        elif vwap_pos == 'ABOVE_SD1' and row['close'] > row.get('vwap', row['close']):
            return "IMBALANCED_BULL"
        elif vwap_pos == 'BELOW_SD1' and row['close'] < row.get('vwap', row['close']):
            return "IMBALANCED_BEAR"
        else:
            return "BALANCED"
    
    def _time_in_session(self, timestamp) -> int:
        """Calculate minutes since 9:30 AM ET."""
        if isinstance(timestamp, str):
            timestamp = pd.to_datetime(timestamp)
        
        # Convert to ET (approximate)
        hour = timestamp.hour - 5  # UTC to ET
        minute = timestamp.minute
        
        # Calculate minutes from 9:30
        minutes = (hour - 9) * 60 + (minute - 30)
        return max(0, minutes)
    
    def run_backtest(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Run the backtest on prepared data.
        """
        logger.info("Running backtest...")
        
        equity = self.account_size
        position = None  # None or {'side': 'LONG'/'SHORT', 'entry': price, 'size': contracts}
        
        for i, row in df.iterrows():
            # Skip if not enough data
            if pd.isna(row.get('vwap')):
                continue
            
            # Check for exit if in position
            if position:
                exit_result = self._check_exit(row, position)
                if exit_result:
                    # Record trade
                    trade = {
                        'entry_time': position['entry_time'],
                        'exit_time': row['timestamp'],
                        'side': position['side'],
                        'entry_price': position['entry_price'],
                        'exit_price': exit_result['exit_price'],
                        'contracts': position['contracts'],
                        'pnl': exit_result['pnl'],
                        'setup': position['setup'],
                        'exit_reason': exit_result['reason']
                    }
                    self.trades.append(trade)
                    
                    # Update equity
                    equity += exit_result['pnl']
                    position = None
            
            # Check for entry if flat
            else:
                signal = self.signal_gen.generate(
                    market_state=row['market_state'],
                    vwap_position=row['vwap_position'],
                    delta_direction=row['delta_direction'],
                    delta_flip=row['delta_flip'],
                    price_at_vwap_band=row['vwap_position'] in ['ABOVE_SD1', 'BELOW_SD1', 'ABOVE_SD2', 'BELOW_SD2'],
                    volume_spike=row['volume_ratio'] > 1.5,
                    session_phase="MID",
                    time_in_session_minutes=row['time_in_session']
                )
                
                if signal['action'] in ['BUY', 'SELL']:
                    # Calculate position size
                    size_result = self.position_sizer.calculate_size(
                        account_equity=equity,
                        entry_price=row['close'],
                        stop_price=row['close'] - 5 if signal['action'] == 'BUY' else row['close'] + 5,
                        atr=row.get('atr', 7.5),
                        signal_confidence=signal['confidence'],
                        market_state=row['market_state']
                    )
                    
                    # Enter position
                    position = {
                        'side': 'LONG' if signal['action'] == 'BUY' else 'SHORT',
                        'entry_price': row['close'],
                        'entry_time': row['timestamp'],
                        'contracts': size_result['contracts'],
                        'setup': signal['setup_type'],
                        'stop': row['close'] - 5 if signal['action'] == 'BUY' else row['close'] + 5,
                        'target': row.get('vwap', row['close']) if 'mean_reversion' in signal['setup_type'].lower() else row['close'] + 10
                    }
        
        # Close any open position at end
        if position:
            last_row = df.iloc[-1]
            exit_result = {
                'exit_price': last_row['close'],
                'pnl': (last_row['close'] - position['entry_price']) * position['contracts'] * self.point_value * (1 if position['side'] == 'LONG' else -1),
                'reason': 'end_of_data'
            }
            
            trade = {
                'entry_time': position['entry_time'],
                'exit_time': last_row['timestamp'],
                'side': position['side'],
                'entry_price': position['entry_price'],
                'exit_price': exit_result['exit_price'],
                'contracts': position['contracts'],
                'pnl': exit_result['pnl'],
                'setup': position['setup'],
                'exit_reason': exit_result['reason']
            }
            self.trades.append(trade)
        
        return self._calculate_metrics()
    
    def _check_exit(self, row: pd.Series, position: Dict) -> Dict:
        """
        Check if position should be exited.
        
        Returns:
            Exit dict or None
        """
        # Check stop loss
        if position['side'] == 'LONG':
            if row['low'] <= position['stop']:
                return {
                    'exit_price': position['stop'],
                    'pnl': (position['stop'] - position['entry_price']) * position['contracts'] * self.point_value,
                    'reason': 'stop_loss'
                }
            
            # Check target
            if row['high'] >= position['target']:
                return {
                    'exit_price': position['target'],
                    'pnl': (position['target'] - position['entry_price']) * position['contracts'] * self.point_value,
                    'reason': 'target'
                }
        
        else:  # SHORT
            if row['high'] >= position['stop']:
                return {
                    'exit_price': position['stop'],
                    'pnl': (position['entry_price'] - position['stop']) * position['contracts'] * self.point_value,
                    'reason': 'stop_loss'
                }
            
            if row['low'] <= position['target']:
                return {
                    'exit_price': position['target'],
                    'pnl': (position['entry_price'] - position['target']) * position['contracts'] * self.point_value,
                    'reason': 'target'
                }
        
        return None
    
    def _calculate_metrics(self) -> Dict[str, Any]:
        """Calculate backtest performance metrics."""
        if not self.trades:
            return {"error": "No trades generated"}
        
        trades_df = pd.DataFrame(self.trades)
        
        # Basic metrics
        total_trades = len(trades_df)
        winning_trades = len(trades_df[trades_df['pnl'] > 0])
        losing_trades = len(trades_df[trades_df['pnl'] <= 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # PnL metrics
        total_pnl = trades_df['pnl'].sum()
        gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].sum()
        profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
        
        avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if winning_trades > 0 else 0
        avg_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].mean() if losing_trades > 0 else 0
        
        # R-multiples (assuming 1% risk per trade)
        trades_df['r_multiple'] = trades_df['pnl'] / (self.account_size * 0.01)
        avg_r = trades_df['r_multiple'].mean()
        
        # Setup performance
        setup_stats = trades_df.groupby('setup').agg({
            'pnl': ['count', 'sum', 'mean'],
            'side': 'count'
        }).round(2)
        
        # Drawdown calculation
        trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
        trades_df['peak'] = trades_df['cumulative_pnl'].cummax()
        trades_df['drawdown'] = trades_df['cumulative_pnl'] - trades_df['peak']
        max_drawdown = trades_df['drawdown'].min()
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': f"{win_rate:.1%}",
            'total_pnl': f"${total_pnl:,.2f}",
            'gross_profit': f"${gross_profit:,.2f}",
            'gross_loss': f"${gross_loss:,.2f}",
            'profit_factor': f"{profit_factor:.2f}",
            'avg_win': f"${avg_win:,.2f}",
            'avg_loss': f"${avg_loss:,.2f}",
            'avg_r_multiple': f"{avg_r:.2f}R",
            'max_drawdown': f"${max_drawdown:,.2f}",
            'setup_stats': setup_stats,
            'trades': trades_df.to_dict('records')
        }
    
    def print_report(self, results: Dict):
        """Print formatted backtest report."""
        print("\n" + "="*70)
        print("BACKTEST RESULTS - FALLBACK STRATEGY".center(70))
        print("="*70 + "\n")
        
        if 'error' in results:
            print(f"ERROR: {results['error']}")
            return
        
        print("OVERALL PERFORMANCE:")
        print("-" * 70)
        print(f"  Total Trades:        {results['total_trades']}")
        print(f"  Win Rate:            {results['win_rate']}")
        print(f"  Total PnL:           {results['total_pnl']}")
        print(f"  Profit Factor:       {results['profit_factor']}")
        print(f"  Average R-Multiple:  {results['avg_r_multiple']}")
        print(f"  Max Drawdown:        {results['max_drawdown']}")
        print()
        
        print("TRADE BREAKDOWN:")
        print("-" * 70)
        print(f"  Winning Trades:      {results['winning_trades']} ({results['win_rate']})")
        print(f"  Losing Trades:       {results['losing_trades']}")
        print(f"  Average Win:         {results['avg_win']}")
        print(f"  Average Loss:        {results['avg_loss']}")
        print()
        
        print("SETUP PERFORMANCE:")
        print("-" * 70)
        print(results['setup_stats'])
        print()
        
        print("RECENT TRADES:")
        print("-" * 70)
        for trade in results['trades'][-5:]:
            print(f"  {trade['entry_time']}: {trade['side']} {trade['setup']} - "
                  f"PnL: ${trade['pnl']:,.2f} ({trade['exit_reason']})")
        print()
        
        print("="*70)
        print("BACKTEST COMPLETE".center(70))
        print("="*70 + "\n")
        
        # Assessment
        win_rate_val = float(results['win_rate'].strip('%')) / 100
        if win_rate_val >= 0.55:
            print("✅ RESULT: Win rate within expected range (55-65%)")
            print("✅ Strategy is ready for live paper trading!")
        else:
            print("⚠️  RESULT: Win rate below 55%")
            print("⚠️  Consider reviewing signal conditions or time period")


def main():
    parser = argparse.ArgumentParser(description='Backtest fallback strategy')
    parser.add_argument('--symbol', default='MES', help='Symbol to trade (default: MES)')
    parser.add_argument('--days', type=int, default=30, help='Days of data (default: 30)')
    parser.add_argument('--account', type=float, default=50000, help='Account size (default: 50000)')
    parser.add_argument('--bar-size', default='5 mins', help='Bar size (default: 5 mins)')
    
    args = parser.parse_args()
    
    print("\n" + "="*70)
    print("FALLBACK STRATEGY BACKTEST".center(70))
    print("="*70 + "\n")
    
    print(f"Symbol:      {args.symbol}")
    print(f"Duration:    {args.days} days")
    print(f"Bar Size:    {args.bar_size}")
    print(f"Account:     ${args.account:,.2f}")
    print()
    
    # Create backtest
    backtest = HistoricalBacktest(symbol=args.symbol, account_size=args.account)
    
    # Fetch data
    df = backtest.fetch_data(duration=f"{args.days} D", bar_size=args.bar_size)
    
    if df.empty:
        print("ERROR: No data fetched. Make sure TWS is running.")
        return 1
    
    # Prepare data
    df = backtest.prepare_data(df)
    
    # Run backtest
    results = backtest.run_backtest(df)
    
    # Print report
    backtest.print_report(results)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
