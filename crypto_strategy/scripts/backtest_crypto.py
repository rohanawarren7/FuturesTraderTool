#!/usr/bin/env python3
"""
Crypto Strategy Backtest
========================

Backtest VWAP mean reversion strategy on Micro Bitcoin Futures (MBT).
Completely separate from MES backtest.

Usage:
    python crypto_strategy/scripts/backtest_crypto.py --file data/mbt_3month.csv
    python crypto_strategy/scripts/backtest_crypto.py --account 50000 --days 90
"""

import sys
from pathlib import Path
# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Import crypto-specific modules
from core.crypto_signal_generator import CryptoSignalGenerator, CryptoVWAPCalculator, CryptoPositionSizer
from config.crypto_instrument_specs import CRYPTO_RISK_CONFIG, CRYPTO_PERFORMANCE_TARGETS


def load_and_prepare_data(filepath: str) -> pd.DataFrame:
    """Load MBT data and calculate indicators."""
    print(f"\nLoading crypto data from {filepath}...")
    
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df = df.sort_values('timestamp')
    
    print(f"Loaded {len(df)} bars")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    
    # Detect if 4H or 15-min data based on number of bars
    timeframe = "4H" if len(df) < 2000 else "15min"
    print(f"Detected timeframe: {timeframe}")
    
    # Calculate VWAP (adjust window based on timeframe)
    if timeframe == "4H":
        print("Calculating daily VWAP (6 bars)...")
        vwap_window = 6  # 1 day of 4H bars
        atr_window = 10
        vol_window = 10
    else:
        print("Calculating 4-hour rolling VWAP (16 bars)...")
        vwap_window = 16  # 4 hours of 15-min bars
        atr_window = 20
        vol_window = 20
    
    vwap_calc = CryptoVWAPCalculator(rolling_window_bars=vwap_window)
    df = vwap_calc.calculate(df)
    
    # Calculate ATR
    print(f"Calculating ATR ({atr_window} bars)...")
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['prev_close'])
    df['tr3'] = abs(df['low'] - df['prev_close'])
    df['true_range'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['true_range'].rolling(window=atr_window, min_periods=1).mean()
    
    # Calculate volume ratio
    df['volume_avg'] = df['volume'].rolling(window=vol_window, min_periods=1).mean()
    df['volume_ratio'] = df['volume'] / df['volume_avg']
    
    # Calculate delta proxy (simplified)
    df['delta'] = df['close'] - df['open']
    df['delta_direction'] = np.where(df['delta'] > 0, 'POSITIVE',
                                     np.where(df['delta'] < 0, 'NEGATIVE', 'NEUTRAL'))
    df['delta_flip'] = df['delta_direction'] != df['delta_direction'].shift(1)
    
    print("Data preparation complete\n")
    return df


def run_crypto_backtest(df: pd.DataFrame, account_size: float = 50000) -> dict:
    """
    Run crypto strategy backtest on prepared data.
    """
    print("="*70)
    print("CRYPTO STRATEGY BACKTEST - MBT (Micro Bitcoin Futures)")
    print("="*70)
    print(f"Account Size: ${account_size:,.2f}")
    print(f"Data Period: {df['timestamp'].min().strftime('%Y-%m-%d')} to {df['timestamp'].max().strftime('%Y-%m-%d')}")
    print("="*70 + "\n")
    
    # Initialize components
    signal_gen = CryptoSignalGenerator(
        use_conservative_mode=True,
        volatility_regime="NORMAL"
    )
    position_sizer = CryptoPositionSizer(
        base_risk_pct=0.005,  # 0.5%
        max_contracts=2
    )
    
    # Tracking variables
    equity = account_size
    initial_equity = account_size
    position = None
    trades = []
    daily_trades = 0
    consecutive_losses = 0
    last_date = None
    max_equity = account_size
    max_drawdown = 0
    
    print("Running backtest...")
    
    for idx, row in df.iterrows():
        current_date = row['timestamp'].date()
        
        # Reset daily counters
        if last_date != current_date:
            daily_trades = 0
            consecutive_losses = 0
            last_date = current_date
        
        # Skip if insufficient data
        if pd.isna(row['vwap']) or pd.isna(row['atr']):
            continue
        
        # Check for exit if in position
        if position is not None:
            exit_price = row['close']
            
            # Calculate PnL
            if position['side'] == 'LONG':
                pnl = (exit_price - position['entry_price']) * position['contracts'] * 0.1
                
                # Check stop loss
                if row['low'] <= position['stop']:
                    pnl = (position['stop'] - position['entry_price']) * position['contracts'] * 0.1
                    exit_reason = 'stop_loss'
                # Check target
                elif row['high'] >= position['target']:
                    exit_reason = 'target_hit'
                else:
                    continue  # Hold position
            else:  # SHORT
                pnl = (position['entry_price'] - exit_price) * position['contracts'] * 0.1
                
                # Check stop loss
                if row['high'] >= position['stop']:
                    pnl = (position['entry_price'] - position['stop']) * position['contracts'] * 0.1
                    exit_reason = 'stop_loss'
                # Check target
                elif row['low'] <= position['target']:
                    exit_reason = 'target_hit'
                else:
                    continue  # Hold position
            
            # Update equity
            equity += pnl
            
            # Track consecutive losses
            if pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            
            # Track drawdown
            if equity > max_equity:
                max_equity = equity
            drawdown = max_equity - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
            # Record trade
            trades.append({
                'entry_time': position['entry_time'],
                'exit_time': row['timestamp'],
                'side': position['side'],
                'setup': position['setup'],
                'contracts': position['contracts'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price if exit_reason != 'stop_loss' else position['stop'],
                'pnl': pnl,
                'exit_reason': exit_reason,
                'r_multiple': pnl / position['risk_amount'] if position['risk_amount'] > 0 else 0
            })
            
            position = None
        
        # Check for new entry if flat
        elif daily_trades < 3 and consecutive_losses < 2:  # Crypto limits
            delta_metrics = {
                'direction': row['delta_direction'],
                'flip': row['delta_flip']
            }
            
            vwap_data = {
                'vwap': row['vwap'],
                'sd1_upper': row['vwap_sd1_upper'],
                'sd1_lower': row['vwap_sd1_lower'],
                'sd2_upper': row['vwap_sd2_upper'],
                'sd2_lower': row['vwap_sd2_lower']
            }
            
            signal = signal_gen.generate(
                bar_data=row,
                timestamp=row['timestamp'],
                vwap_data=vwap_data,
                delta_metrics=delta_metrics
            )
            
            if signal['action'] != 'HOLD':
                # Calculate position size
                size_result = position_sizer.calculate_size(
                    account_equity=equity,
                    entry_price=row['close'],
                    stop_price=signal['stop_price'],
                    atr=row['atr'],
                    volatility_regime="NORMAL"
                )
                
                if size_result['contracts'] > 0:
                    # Enter position
                    position = {
                        'side': 'LONG' if signal['action'] == 'BUY' else 'SHORT',
                        'entry_price': row['close'],
                        'entry_time': row['timestamp'],
                        'contracts': size_result['contracts'],
                        'setup': signal['setup_type'],
                        'stop': signal['stop_price'],
                        'target': signal['target_price'],
                        'risk_amount': size_result['risk_amount']
                    }
                    daily_trades += 1
    
    # Calculate results
    if len(trades) == 0:
        return {'error': 'No trades generated', 'total_trades': 0}
    
    trades_df = pd.DataFrame(trades)
    
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['pnl'] > 0])
    losing_trades = len(trades_df[trades_df['pnl'] <= 0])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    
    total_pnl = trades_df['pnl'].sum()
    gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
    gross_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].sum()
    profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
    
    avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if winning_trades > 0 else 0
    avg_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].mean() if losing_trades > 0 else 0
    
    avg_r = trades_df['r_multiple'].mean()
    
    final_equity = equity
    return_pct = (final_equity - initial_equity) / initial_equity * 100
    
    # Setup stats
    setup_stats = {}
    for setup in trades_df['setup'].unique():
        setup_trades = trades_df[trades_df['setup'] == setup]
        setup_stats[setup] = {
            'trades': len(setup_trades),
            'pnl': setup_trades['pnl'].sum(),
            'win_rate': len(setup_trades[setup_trades['pnl'] > 0]) / len(setup_trades)
        }
    
    return {
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'avg_r': avg_r,
        'max_drawdown': max_drawdown,
        'initial_equity': initial_equity,
        'final_equity': final_equity,
        'return_pct': return_pct,
        'setup_stats': setup_stats,
        'trades': trades_df.to_dict('records'),
        'r_multiples': trades_df['r_multiple'].tolist()
    }


def print_crypto_report(results: dict):
    """Print formatted crypto backtest report."""
    print("\n" + "="*70)
    print("CRYPTO BACKTEST RESULTS - MBT STRATEGY".center(70))
    print("="*70 + "\n")
    
    if 'error' in results:
        print(f"ERROR: {results['error']}")
        return
    
    print("OVERALL PERFORMANCE:")
    print("-" * 70)
    print(f"  Total Trades:        {results['total_trades']}")
    print(f"  Win Rate:            {results['win_rate']:.1%}")
    print(f"  Total PnL:           ${results['total_pnl']:,.2f}")
    print(f"  Return:              {results['return_pct']:.2f}%")
    print(f"  Profit Factor:       {results['profit_factor']:.2f}")
    print(f"  Average R:           {results['avg_r']:.2f}R")
    print(f"  Max Drawdown:        ${results['max_drawdown']:,.2f} ({results['max_drawdown']/results['initial_equity']*100:.1f}%)")
    print()
    
    print("TRADE BREAKDOWN:")
    print("-" * 70)
    print(f"  Winning Trades:      {results['winning_trades']} ({results['win_rate']:.1%})")
    print(f"  Losing Trades:       {results['losing_trades']}")
    print(f"  Average Win:         ${results['avg_win']:,.2f}")
    print(f"  Average Loss:        ${results['avg_loss']:,.2f}")
    print()
    
    print("SETUP PERFORMANCE:")
    print("-" * 70)
    for setup, stats in results['setup_stats'].items():
        print(f"  {setup:30s}: {stats['trades']:2d} trades, "
              f"${stats['pnl']:>8,.2f}, {stats['win_rate']:.0%} win rate")
    print()
    
    print("RECENT TRADES:")
    print("-" * 70)
    for trade in results['trades'][-5:]:
        ts = pd.to_datetime(trade['entry_time']).strftime('%m/%d %H:%M')
        print(f"  {ts}: {trade['side']:5s} {trade['setup']:30s} "
              f"PnL: ${trade['pnl']:>7,.2f} ({trade['exit_reason']})")
    print()
    
    print("="*70)
    # Evaluation based on crypto targets
    targets = CRYPTO_PERFORMANCE_TARGETS
    pf = results['profit_factor']
    
    if pf >= targets['excellent_performance']['profit_factor']:
        print("[PASS] Excellent crypto performance!")
        print("[PASS] Ready for paper trading")
    elif pf >= targets['good_performance']['profit_factor']:
        print("[PASS] Good crypto performance")
        print("[PASS] Proceed with paper trading")
    elif pf >= targets['minimum_viable']['profit_factor']:
        print("[PASS] Minimum viable for crypto")
        print("[WARN] Monitor closely in paper mode")
    else:
        print("[FAIL] Below minimum threshold")
        print("[FAIL] Do not trade live")
    
    print(f"\n  Win Rate: {results['win_rate']:.1%} | "
          f"PF: {pf:.2f} | "
          f"Expectancy: {results['avg_r']:.2f}R | "
          f"Max DD: {results['max_drawdown']/results['initial_equity']*100:.1f}%")
    print("="*70 + "\n")
    
    # Compare to targets
    print("COMPARISON TO CRYPTO TARGETS:")
    print("-" * 70)
    min_targets = targets['minimum_viable']
    print(f"  Profit Factor: {pf:.2f} vs {min_targets['profit_factor']:.1f} min")
    print(f"  Win Rate: {results['win_rate']:.1%} vs {min_targets['win_rate']:.0%} min")
    print(f"  Expectancy: {results['avg_r']:.2f}R vs {min_targets['expectancy_r']:.1f}R min")
    print(f"  Max DD: {results['max_drawdown']/results['initial_equity']*100:.1f}% vs {min_targets['max_drawdown_pct']:.0%} max")
    print("="*70 + "\n")


def main():
    parser = argparse.ArgumentParser(description='Crypto strategy backtest for MBT')
    parser.add_argument('--file', type=str,
                       help='CSV file with MBT data')
    parser.add_argument('--account', type=float, default=50000,
                       help='Starting account size')
    parser.add_argument('--days', type=int, default=90,
                       help='Days to download if no file provided')
    
    args = parser.parse_args()
    
    if args.file:
        # Load provided file
        df = load_and_prepare_data(args.file)
    else:
        print("ERROR: Please provide --file with MBT data")
        print("Download from IBKR first:")
        print("  python crypto_strategy/scripts/download_mbt_data.py")
        return 1
    
    # Run backtest
    results = run_crypto_backtest(df, args.account)
    
    # Print report
    print_crypto_report(results)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
