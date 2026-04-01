#!/usr/bin/env python3
"""
Demo Backtest with Synthetic Data
=================================

Shows how the backtest works without needing live market data.
Generates realistic MES price action for testing.

Usage:
    python scripts/demo_backtest.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from core.signal_generator import SignalGenerator
from core.risk_manager import RiskManager
from core.position_sizer import PositionSizer
from core.vwap_calculator import VWAPCalculator
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS


def generate_synthetic_data(days=30, bars_per_day=78):
    """
    Generate synthetic MES price data that looks realistic.
    
    Args:
        days: Number of trading days
        bars_per_day: 5-min bars per day (78 for 9:30-16:00)
    """
    print("Generating synthetic MES data...")
    
    np.random.seed(42)  # For reproducible results
    
    data = []
    start_date = datetime.now() - timedelta(days=days)
    
    # Starting price
    price = 5000.0
    
    for day in range(days):
        date = start_date + timedelta(days=day)
        
        # Skip weekends
        if date.weekday() >= 5:
            continue
        
        # Daily drift and volatility
        daily_drift = np.random.normal(0, 0.001)  # Small random drift
        daily_vol = np.random.uniform(0.003, 0.008)  # 0.3-0.8% daily vol
        
        # VWAP anchor for the day
        vwap_anchor = price * (1 + daily_drift)
        
        for bar in range(bars_per_day):
            # Time for this bar (9:30 AM + bar*5 minutes)
            bar_time = date.replace(hour=9, minute=30) + timedelta(minutes=bar*5)
            
            # Mean reversion to VWAP
            mean_reversion = (vwap_anchor - price) * 0.1
            
            # Random walk
            noise = np.random.normal(0, daily_vol / np.sqrt(bars_per_day))
            
            # Update price
            price = price * (1 + mean_reversion + noise)
            
            # Generate OHLC around price
            bar_vol = daily_vol / np.sqrt(bars_per_day)
            open_p = price * (1 + np.random.normal(0, bar_vol * 0.3))
            high_p = max(open_p, price) * (1 + abs(np.random.normal(0, bar_vol * 0.5)))
            low_p = min(open_p, price) * (1 - abs(np.random.normal(0, bar_vol * 0.5)))
            close_p = price
            
            # Volume (higher at open/close)
            hour = bar_time.hour + bar_time.minute / 60
            if 9.5 <= hour <= 10 or 15.5 <= hour <= 16:
                base_vol = np.random.randint(8000, 15000)
            elif 11 <= hour <= 14:
                base_vol = np.random.randint(3000, 6000)
            else:
                base_vol = np.random.randint(5000, 10000)
            
            data.append({
                'timestamp': bar_time,
                'open': round(open_p, 2),
                'high': round(high_p, 2),
                'low': round(low_p, 2),
                'close': round(close_p, 2),
                'volume': base_vol
            })
    
    df = pd.DataFrame(data)
    print(f"Generated {len(df)} bars of synthetic data")
    return df


class DemoBacktest:
    """Demo backtest using synthetic data."""
    
    def __init__(self, account_size=50000):
        self.account_size = account_size
        self.point_value = 5  # MES
        
        # Components
        self.prop_config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
        self.risk_manager = RiskManager(self.prop_config)
        self.position_sizer = PositionSizer(self.risk_manager, INSTRUMENT_SPECS["MES"])
        self.signal_gen = SignalGenerator(video_trade_count=0, use_fallback=True)
        self.vwap_calc = VWAPCalculator()
        
        self.trades = []
    
    def prepare_data(self, df):
        """Add indicators."""
        # VWAP
        df = self.vwap_calc.calculate_session_vwap(df)
        
        # ATR
        df['atr'] = self._calculate_atr(df)
        
        # Volume ratio
        df['volume_avg'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_avg']
        
        # Delta (close - open)
        df['delta'] = df['close'] - df['open']
        df['delta_direction'] = df['delta'].apply(
            lambda x: 'POSITIVE' if x > 0 else ('NEGATIVE' if x < 0 else 'NEUTRAL')
        )
        df['delta_flip'] = (
            (df['delta'] > 0) & (df['delta'].shift(1) <= 0) |
            (df['delta'] < 0) & (df['delta'].shift(1) >= 0)
        )
        
        # VWAP position
        df['vwap_position'] = df.apply(self._get_vwap_position, axis=1)
        
        # Market state
        df['market_state'] = df.apply(self._detect_market_state, axis=1)
        
        # Time in session
        df['time_in_session'] = df['timestamp'].apply(self._time_in_session)
        
        return df
    
    def _calculate_atr(self, df, period=14):
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()
    
    def _get_vwap_position(self, row):
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
    
    def _detect_market_state(self, row):
        vwap_pos = row.get('vwap_position', 'INSIDE_SD1')
        close = row['close']
        vwap = row.get('vwap', close)
        
        if vwap_pos == 'ABOVE_SD1' and close > vwap:
            return "IMBALANCED_BULL"
        elif vwap_pos == 'BELOW_SD1' and close < vwap:
            return "IMBALANCED_BEAR"
        else:
            return "BALANCED"
    
    def _time_in_session(self, timestamp):
        if isinstance(timestamp, str):
            timestamp = pd.to_datetime(timestamp)
        hour = timestamp.hour - 5  # UTC to ET
        minute = timestamp.minute
        return max(0, (hour - 9) * 60 + (minute - 30))
    
    def run_backtest(self, df):
        """Run backtest."""
        print("Running backtest...")
        
        equity = self.account_size
        position = None
        
        for i, row in df.iterrows():
            if pd.isna(row.get('vwap')):
                continue
            
            # Check exit
            if position:
                exit_result = self._check_exit(row, position)
                if exit_result:
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
                    equity += exit_result['pnl']
                    position = None
            
            # Check entry
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
                    # Calculate size
                    size_result = self.position_sizer.calculate_size(
                        account_equity=equity,
                        entry_price=row['close'],
                        stop_price=row['close'] - 5 if signal['action'] == 'BUY' else row['close'] + 5,
                        atr=row.get('atr', 7.5),
                        signal_confidence=signal['confidence'],
                        market_state=row['market_state']
                    )
                    
                    position = {
                        'side': 'LONG' if signal['action'] == 'BUY' else 'SHORT',
                        'entry_price': row['close'],
                        'entry_time': row['timestamp'],
                        'contracts': size_result['contracts'],
                        'setup': signal['setup_type'],
                        'stop': row['close'] - 5 if signal['action'] == 'BUY' else row['close'] + 5,
                        'target': row.get('vwap', row['close'])
                    }
        
        # Close final position
        if position:
            last_row = df.iloc[-1]
            pnl = (last_row['close'] - position['entry_price']) * position['contracts'] * self.point_value
            if position['side'] == 'SHORT':
                pnl = -pnl
            
            self.trades.append({
                'entry_time': position['entry_time'],
                'exit_time': last_row['timestamp'],
                'side': position['side'],
                'entry_price': position['entry_price'],
                'exit_price': last_row['close'],
                'contracts': position['contracts'],
                'pnl': pnl,
                'setup': position['setup'],
                'exit_reason': 'end_of_data'
            })
        
        return self._calculate_metrics(equity)
    
    def _check_exit(self, row, position):
        """Check for exit conditions."""
        if position['side'] == 'LONG':
            if row['low'] <= position['stop']:
                return {'exit_price': position['stop'], 'pnl': (position['stop'] - position['entry_price']) * position['contracts'] * self.point_value, 'reason': 'stop_loss'}
            if row['high'] >= position['target']:
                return {'exit_price': position['target'], 'pnl': (position['target'] - position['entry_price']) * position['contracts'] * self.point_value, 'reason': 'target'}
        else:
            if row['high'] >= position['stop']:
                return {'exit_price': position['stop'], 'pnl': (position['entry_price'] - position['stop']) * position['contracts'] * self.point_value, 'reason': 'stop_loss'}
            if row['low'] <= position['target']:
                return {'exit_price': position['target'], 'pnl': (position['entry_price'] - position['target']) * position['contracts'] * self.point_value, 'reason': 'target'}
        return None
    
    def _calculate_metrics(self, final_equity):
        """Calculate performance metrics."""
        if not self.trades:
            return {"error": "No trades generated"}
        
        trades_df = pd.DataFrame(self.trades)
        
        total_trades = len(trades_df)
        winning_trades = len(trades_df[trades_df['pnl'] > 0])
        losing_trades = len(trades_df[trades_df['pnl'] <= 0])
        win_rate = winning_trades / total_trades
        
        total_pnl = trades_df['pnl'].sum()
        gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].sum()
        profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
        
        avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if winning_trades > 0 else 0
        avg_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].mean() if losing_trades > 0 else 0
        
        trades_df['r_multiple'] = trades_df['pnl'] / (self.account_size * 0.01)
        avg_r = trades_df['r_multiple'].mean()
        
        trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
        trades_df['peak'] = trades_df['cumulative_pnl'].cummax()
        trades_df['drawdown'] = trades_df['cumulative_pnl'] - trades_df['peak']
        max_drawdown = trades_df['drawdown'].min()
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_r': avg_r,
            'max_drawdown': max_drawdown,
            'trades': trades_df.to_dict('records')
        }
    
    def print_report(self, results):
        """Print formatted report."""
        print("\n" + "="*70)
        print("DEMO BACKTEST RESULTS - FALLBACK STRATEGY".center(70))
        print("="*70 + "\n")
        
        if 'error' in results:
            print(f"ERROR: {results['error']}")
            return
        
        print("OVERALL PERFORMANCE:")
        print("-" * 70)
        print(f"  Total Trades:        {results['total_trades']}")
        print(f"  Win Rate:            {results['win_rate']:.1%}")
        print(f"  Total PnL:           ${results['total_pnl']:,.2f}")
        print(f"  Profit Factor:       {results['profit_factor']:.2f}")
        print(f"  Average R:           {results['avg_r']:.2f}R")
        print(f"  Max Drawdown:        ${results['max_drawdown']:,.2f}")
        print()
        
        print("TRADE BREAKDOWN:")
        print("-" * 70)
        print(f"  Winning Trades:      {results['winning_trades']} ({results['win_rate']:.1%})")
        print(f"  Losing Trades:       {results['losing_trades']}")
        print(f"  Average Win:         ${results['avg_win']:,.2f}")
        print(f"  Average Loss:        ${results['avg_loss']:,.2f}")
        print()
        
        # Setup breakdown
        trades_df = pd.DataFrame(results['trades'])
        print("SETUP PERFORMANCE:")
        print("-" * 70)
        for setup in trades_df['setup'].unique():
            setup_trades = trades_df[trades_df['setup'] == setup]
            wins = len(setup_trades[setup_trades['pnl'] > 0])
            total = len(setup_trades)
            pnl = setup_trades['pnl'].sum()
            print(f"  {setup:25s}: {total:2d} trades, ${pnl:>8,.2f}, {wins/total:.1%} win rate")
        print()
        
        print("RECENT TRADES:")
        print("-" * 70)
        for trade in results['trades'][-5:]:
            ts = pd.to_datetime(trade['entry_time']).strftime('%m/%d %H:%M')
            print(f"  {ts}: {trade['side']:5s} {trade['setup']:25s} PnL: ${trade['pnl']:>7,.2f}")
        print()
        
        print("="*70)
        if results['win_rate'] >= 0.50:
            print("✅ RESULT: Win rate acceptable (above 50%)")
            print("✅ Strategy ready for paper trading!")
        else:
            print("⚠️  Win rate below 50% - review needed")
        print("="*70 + "\n")


def main():
    print("="*70)
    print("DEMO BACKTEST - FALLBACK STRATEGY")
    print("="*70)
    print("\nUsing synthetic MES data (no live connection needed)\n")
    
    # Generate data
    df = generate_synthetic_data(days=30, bars_per_day=78)
    
    # Run backtest
    backtest = DemoBacktest(account_size=50000)
    df = backtest.prepare_data(df)
    results = backtest.run_backtest(df)
    
    # Print report
    backtest.print_report(results)
    
    print("\nNote: This is synthetic data for demonstration.")
    print("Run on real data with: python scripts/backtest_fallback.py")


if __name__ == "__main__":
    main()
