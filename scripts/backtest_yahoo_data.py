#!/usr/bin/env python3
"""
Backtest with Yahoo Finance Data
================================

Runs the fallback VWAP strategy on real MES data downloaded from Yahoo Finance.

Usage:
    python scripts/backtest_yahoo_data.py --file data/yahoo/MES_F_20260321.csv
    python scripts/backtest_yahoo_data.py --days 30
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime

# Try to import matplotlib, but make it optional
try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("[INFO] matplotlib not available - charts will not be generated")

from core.signal_generator import SignalGenerator
from core.risk_manager import RiskManager
from core.position_sizer import PositionSizer
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS


def load_yahoo_data(filepath: str) -> pd.DataFrame:
    """Load and prepare Yahoo Finance data."""
    print(f"Loading data from {filepath}...")
    df = pd.read_csv(filepath)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    print(f"Loaded {len(df)} bars")
    return df


def run_backtest(df: pd.DataFrame, account_size: float = 50000) -> dict:
    """
    Run fallback strategy backtest on prepared data.
    """
    print("\nRunning backtest...")
    
    # Initialize components
    prop_config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
    risk_mgr = RiskManager(prop_config)
    position_sizer = PositionSizer(risk_mgr, INSTRUMENT_SPECS["MES"])
    signal_gen = SignalGenerator()
    
    trades = []
    equity = account_size
    position = None
    daily_trades = 0
    last_date = None
    
    for i, row in df.iterrows():
        # Skip if no VWAP (first bars of session)
        if pd.isna(row.get('vwap')):
            continue
        
        # Reset daily counters at new day
        current_date = row['timestamp'].tz_convert('UTC').date()
        if last_date != current_date:
            risk_mgr.reset_daily_counters(current_date)
            daily_trades = 0
            last_date = current_date
        
        # Check for exit if in position
        if position:
            exit_result = check_exit(row, position)
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
                trades.append(trade)
                equity += exit_result['pnl']
                risk_mgr.update_after_trade({'net_pnl': exit_result['pnl']})
                position = None
                daily_trades += 1
        
        # Check for entry if flat
        elif daily_trades < 5:  # Max 5 per day
            signal = signal_gen.generate(
                market_state=row['market_state'],
                vwap_position=row['vwap_position'],
                delta_direction=row['delta_direction'],
                delta_flip=row['delta_flip'],
                price_at_vwap_band=row['vwap_position'] in ['ABOVE_SD1', 'BELOW_SD1', 'ABOVE_SD2', 'BELOW_SD2'],
                volume_spike=row['volume_ratio'] > 1.5 if not pd.isna(row['volume_ratio']) else False,
                session_phase="MID",
                time_in_session_minutes=row['time_in_session'],
                timestamp=row['timestamp']
            )
            
            if signal['action'] in ['BUY', 'SELL']:
                # Check risk limits
                market_state = {'timestamp': row['timestamp'], 'hour': row['timestamp'].hour}
                account_state = {'equity': equity, 'mll_floor': account_size - 2000, 'positions': []}
                
                allowed, reason = risk_mgr.can_trade(market_state, account_state)
                
                if allowed:
                    # Calculate stop and target based on signal type
                    atr = row.get('atr', 7.5) if not pd.isna(row.get('atr')) else 7.5
                    
                    # Use 1.5x ATR for stop (more reasonable than fixed 5 ticks)
                    if signal['action'] == 'BUY':
                        stop_price = row['close'] - (atr * 1.5)
                        # Target: VWAP for mean reversion, SD1 band for others
                        if 'MEAN_REVERSION' in signal['setup_type']:
                            target_price = row.get('vwap', row['close'] + (atr * 1.0))
                        else:
                            target_price = row.get('vwap_sd1_upper', row['close'] + (atr * 1.0))
                    else:  # SELL
                        stop_price = row['close'] + (atr * 1.5)
                        if 'MEAN_REVERSION' in signal['setup_type']:
                            target_price = row.get('vwap', row['close'] - (atr * 1.0))
                        else:
                            target_price = row.get('vwap_sd1_lower', row['close'] - (atr * 1.0))
                    
                    size_result = position_sizer.calculate_size(
                        account_equity=equity,
                        entry_price=row['close'],
                        stop_price=stop_price,
                        atr=atr,
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
                        'stop': stop_price,
                        'target': target_price
                    }
    
    # Close final position at end of data
    if position:
        last_row = df.iloc[-1]
        if position['side'] == 'LONG':
            pnl = (last_row['close'] - position['entry_price']) * position['contracts'] * 5
        else:
            pnl = (position['entry_price'] - last_row['close']) * position['contracts'] * 5
        
        trades.append({
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
    
    return calculate_metrics(trades, account_size, equity)


def check_exit(row: pd.Series, position: dict) -> dict:
    """Check if position should be exited."""
    point_value = 5  # MES
    
    if position['side'] == 'LONG':
        # Stop loss
        if row['low'] <= position['stop']:
            return {
                'exit_price': position['stop'],
                'pnl': (position['stop'] - position['entry_price']) * position['contracts'] * point_value,
                'reason': 'stop_loss'
            }
        # Target hit
        if row['high'] >= position['target']:
            return {
                'exit_price': position['target'],
                'pnl': (position['target'] - position['entry_price']) * position['contracts'] * point_value,
                'reason': 'target'
            }
    else:  # SHORT
        # Stop loss
        if row['high'] >= position['stop']:
            return {
                'exit_price': position['stop'],
                'pnl': (position['entry_price'] - position['stop']) * position['contracts'] * point_value,
                'reason': 'stop_loss'
            }
        # Target hit
        if row['low'] <= position['target']:
            return {
                'exit_price': position['target'],
                'pnl': (position['entry_price'] - position['target']) * position['contracts'] * point_value,
                'reason': 'target'
            }
    
    return None


def calculate_metrics(trades: list, initial_equity: float, final_equity: float) -> dict:
    """Calculate performance metrics."""
    if not trades:
        return {'error': 'No trades generated'}
    
    trades_df = pd.DataFrame(trades)
    
    # Basic stats
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['pnl'] > 0])
    losing_trades = len(trades_df[trades_df['pnl'] <= 0])
    win_rate = winning_trades / total_trades if total_trades > 0 else 0
    
    # PnL stats
    total_pnl = trades_df['pnl'].sum()
    gross_profit = trades_df[trades_df['pnl'] > 0]['pnl'].sum()
    gross_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].sum()
    profit_factor = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')
    
    avg_win = trades_df[trades_df['pnl'] > 0]['pnl'].mean() if winning_trades > 0 else 0
    avg_loss = trades_df[trades_df['pnl'] <= 0]['pnl'].mean() if losing_trades > 0 else 0
    
    # R-multiples
    trades_df['r_multiple'] = trades_df['pnl'] / (initial_equity * 0.01)
    avg_r = trades_df['r_multiple'].mean()
    
    # Drawdown
    trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
    trades_df['peak'] = trades_df['cumulative_pnl'].cummax()
    trades_df['drawdown'] = trades_df['cumulative_pnl'] - trades_df['peak']
    max_drawdown = trades_df['drawdown'].min()
    
    # Setup breakdown
    setup_stats = {}
    for setup in trades_df['setup'].unique():
        setup_trades = trades_df[trades_df['setup'] == setup]
        wins = len(setup_trades[setup_trades['pnl'] > 0])
        total = len(setup_trades)
        pnl = setup_trades['pnl'].sum()
        setup_stats[setup] = {
            'trades': total,
            'wins': wins,
            'win_rate': wins / total if total > 0 else 0,
            'pnl': pnl
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
        'return_pct': (final_equity - initial_equity) / initial_equity * 100,
        'setup_stats': setup_stats,
        'trades': trades_df.to_dict('records'),
        'r_multiples': trades_df['r_multiple'].tolist()
    }


def print_report(results: dict):
    """Print formatted backtest report."""
    print("\n" + "="*70)
    print("BACKTEST RESULTS - MES FALLBACK STRATEGY".center(70))
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
    print(f"  Max Drawdown:        ${results['max_drawdown']:,.2f}")
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
        print(f"  {setup:25s}: {stats['trades']:2d} trades, "
              f"${stats['pnl']:>8,.2f}, {stats['win_rate']:.0%} win rate")
    print()
    
    print("RECENT TRADES:")
    print("-" * 70)
    for trade in results['trades'][-5:]:
        ts = pd.to_datetime(trade['entry_time']).strftime('%m/%d %H:%M')
        print(f"  {ts}: {trade['side']:5s} {trade['setup']:25s} "
              f"PnL: ${trade['pnl']:>7,.2f} ({trade['exit_reason']})")
    print()
    
    print("="*70)
    # Use profit factor as primary metric (not win rate) - accounts for risk/reward
    pf = results.get('profit_factor', 0)
    if pf >= 2.0:
        print("[PASS] Profit factor excellent (>= 2.0)")
        print("[PASS] Strategy is ready for live paper trading!")
    elif pf >= 1.5:
        print("[PASS] Profit factor acceptable (1.5-2.0)")
        print("[PASS] Strategy shows positive expectancy")
    elif pf >= 1.0:
        print("[WARN] Profit factor marginal (1.0-1.5)")
        print("[WARN] Review risk management before going live")
    else:
        print("[FAIL] Profit factor below 1.0 - strategy unprofitable")
        print("[FAIL] Risk of ruin - do not trade live")
    print(f"\n  Win Rate: {results['win_rate']:.1%} | Profit Factor: {pf:.2f} | Expectancy: {results.get('avg_r', 0):.3f}R")
    print("="*70 + "\n")
    
    # Print R distribution histogram
    plot_r_distribution(results)
    
    # Print per-setup R stats
    print_per_setup_r_stats(results)
    
    # Print time-of-day analysis
    print_time_of_day_analysis(results)
    
    # Print PnL by week and month
    print_pnl_by_period(results)


def plot_r_distribution(results: dict, output_file: str = 'r_distribution.png'):
    """
    Create and display R-multiple distribution histogram.
    """
    if 'r_multiples' not in results or len(results['r_multiples']) == 0:
        print("No R-multiples data available for histogram")
        return
    
    r_values = np.array(results['r_multiples'])
    
    # Create matplotlib figure if available
    if MATPLOTLIB_AVAILABLE:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
        
        # Histogram
        bins = np.linspace(min(r_values) - 0.1, max(r_values) + 0.1, 20)
        ax1.hist(r_values, bins=bins, edgecolor='black', alpha=0.7)
        ax1.axvline(x=0, color='black', linestyle='--', linewidth=2, label='Breakeven')
        ax1.axvline(x=results['avg_r'], color='blue', linestyle='-', linewidth=2, 
                    label=f"Avg R: {results['avg_r']:.2f}")
        ax1.set_xlabel('R-Multiple')
        ax1.set_ylabel('Frequency')
        ax1.set_title('R-Multiple Distribution')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Box plot
        ax2.boxplot([r_values], labels=['All Trades'])
        ax2.axhline(y=0, color='black', linestyle='--', alpha=0.5)
        ax2.set_ylabel('R-Multiple')
        ax2.set_title('R-Multiple Box Plot')
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
    
    # Print text-based histogram as well
    print("\nR-MULTIPLE DISTRIBUTION (Text):")
    print("-" * 70)
    
    # Create bins for text histogram
    hist, bin_edges = np.histogram(r_values, bins=12)
    max_count = max(hist) if max(hist) > 0 else 1
    
    for i in range(len(hist)):
        bin_range = f"{bin_edges[i]:6.2f} to {bin_edges[i+1]:6.2f}"
        bar = "#" * int(40 * hist[i] / max_count)
        print(f"  {bin_range} : {bar} ({hist[i]})")
    
    print("-" * 70)
    print(f"  Winners (>0R): {sum(r_values > 0)}  |  Losers (<0R): {sum(r_values < 0)}")
    print(f"  Average R: {results['avg_r']:.3f}")
    print(f"  Best Trade: {max(r_values):.3f}R  |  Worst Trade: {min(r_values):.3f}R")
    print(f"  Std Dev: {np.std(r_values):.3f}R")
    print("-" * 70)
    
    # Save to file if matplotlib available
    if MATPLOTLIB_AVAILABLE:
        try:
            plt.savefig(output_file, dpi=150, bbox_inches='tight')
            print(f"\n[SAVED] R distribution chart: {output_file}")
        except Exception as e:
            print(f"\n[WARNING] Could not save chart: {e}")
        
        plt.close()


def print_per_setup_r_stats(results: dict):
    """
    Print R-multiple statistics for each setup type.
    """
    if 'trades' not in results or len(results['trades']) == 0:
        return
    
    trades_df = pd.DataFrame(results['trades'])
    
    print("\nPER-SETUP R-MULTIPLE STATISTICS:")
    print("=" * 70)
    
    for setup in sorted(trades_df['setup'].unique()):
        setup_trades = trades_df[trades_df['setup'] == setup]
        
        winners = setup_trades[setup_trades['pnl'] > 0]
        losers = setup_trades[setup_trades['pnl'] <= 0]
        
        if len(winners) > 0:
            avg_win_r = winners['pnl'].mean() / (results['initial_equity'] * 0.01)
            max_win_r = winners['pnl'].max() / (results['initial_equity'] * 0.01)
        else:
            avg_win_r = 0
            max_win_r = 0
            
        if len(losers) > 0:
            avg_loss_r = losers['pnl'].mean() / (results['initial_equity'] * 0.01)
            max_loss_r = losers['pnl'].min() / (results['initial_equity'] * 0.01)
        else:
            avg_loss_r = 0
            max_loss_r = 0
        
        total_trades = len(setup_trades)
        win_rate = len(winners) / total_trades if total_trades > 0 else 0
        
        # Calculate expectancy
        expectancy = (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)
        
        print(f"\n  {setup}:")
        print(f"    Total Trades:     {total_trades}")
        print(f"    Win Rate:         {win_rate:.1%} ({len(winners)}/{total_trades})")
        print(f"    Avg Win:          {avg_win_r:+.3f}R (max: {max_win_r:+.3f}R)")
        print(f"    Avg Loss:         {avg_loss_r:+.3f}R (max: {max_loss_r:+.3f}R)")
        print(f"    Win/Loss Ratio:   {abs(avg_win_r/avg_loss_r):.2f}:1")
        print(f"    Expectancy:       {expectancy:+.3f}R per trade")
    
    print("\n" + "=" * 70)


def print_time_of_day_analysis(results: dict):
    """
    Analyze trade performance by time of day.
    """
    if 'trades' not in results or len(results['trades']) == 0:
        return
    
    trades_df = pd.DataFrame(results['trades'])
    trades_df['hour'] = pd.to_datetime(trades_df['entry_time']).dt.hour
    trades_df['minute'] = pd.to_datetime(trades_df['entry_time']).dt.minute
    trades_df['time'] = trades_df['hour'] + trades_df['minute'] / 60
    trades_df['time_str'] = pd.to_datetime(trades_df['entry_time']).dt.strftime('%H:%M')
    
    print("\nTIME-OF-DAY ANALYSIS:")
    print("=" * 70)
    
    # Hourly breakdown for Globex evening (19:00-24:00) - where all trades occur
    print("\n  HOURLY BREAKDOWN (19:00-24:00) - WINS vs LOSSES:")
    print("  " + "-" * 66)
    
    for hour in range(19, 24):
        hour_trades = trades_df[trades_df['hour'] == hour]
        
        if len(hour_trades) == 0:
            continue
        
        winners = hour_trades[hour_trades['pnl'] > 0]
        losers = hour_trades[hour_trades['pnl'] <= 0]
        
        total_pnl = hour_trades['pnl'].sum()
        win_rate = len(winners) / len(hour_trades) if len(hour_trades) > 0 else 0
        
        # Show winner times and PnL
        winner_str = ""
        if len(winners) > 0:
            winner_details = [f"{row['time_str']}(${row['pnl']:+.0f})" for _, row in winners.iterrows()]
            winner_str = " | ".join(winner_details)
        
        # Show loser times and PnL
        loser_str = ""
        if len(losers) > 0:
            loser_details = [f"{row['time_str']}(${row['pnl']:+.0f})" for _, row in losers.iterrows()]
            loser_str = " | ".join(loser_details)
        
        print(f"\n  {hour}:00-{hour}:59  |  {len(hour_trades):2d} trades  |  "
              f"Win Rate: {win_rate:.0%}  |  Total: ${total_pnl:>+8,.2f}")
        
        if winner_str:
            print(f"    [WINS]  {winner_str}")
        if loser_str:
            print(f"    [LOSS]  {loser_str}")
    
    print("\n" + "=" * 70)
    
    # Specific analysis for 20:45 threshold
    print("\n  20:45 THRESHOLD ANALYSIS:")
    print("  " + "-" * 66)
    
    before_2045 = trades_df[
        ((trades_df['hour'] == 20) & (trades_df['minute'] < 45)) |
        (trades_df['hour'] < 20)
    ]
    after_2045 = trades_df[
        ((trades_df['hour'] == 20) & (trades_df['minute'] >= 45)) |
        (trades_df['hour'] > 20)
    ]
    
    for label, df_section in [("Before 20:45", before_2045), ("After 20:45", after_2045)]:
        if len(df_section) == 0:
            continue
        
        winners = df_section[df_section['pnl'] > 0]
        losers = df_section[df_section['pnl'] <= 0]
        total_pnl = df_section['pnl'].sum()
        win_rate = len(winners) / len(df_section) if len(df_section) > 0 else 0
        avg_pnl = df_section['pnl'].mean()
        
        print(f"\n  {label}:")
        print(f"    Trades: {len(df_section):2d}  |  Win Rate: {win_rate:.0%}  |  "
              f"Avg PnL: ${avg_pnl:>+7,.2f}  |  Total: ${total_pnl:>+8,.2f}")
    
    print("\n" + "=" * 70)


def print_pnl_by_period(results: dict):
    """
    Analyze PnL breakdown by week and month.
    """
    if 'trades' not in results or len(results['trades']) == 0:
        return
    
    trades_df = pd.DataFrame(results['trades'])
    trades_df['entry_time'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['date'] = trades_df['entry_time'].dt.date
    trades_df['week'] = trades_df['entry_time'].dt.isocalendar().week
    trades_df['year_week'] = trades_df['entry_time'].dt.strftime('%Y-W%W')
    trades_df['month'] = trades_df['entry_time'].dt.strftime('%Y-%m')
    
    print("\nPnL BREAKDOWN BY WEEK:")
    print("=" * 70)
    
    # Weekly breakdown
    weekly_pnl = trades_df.groupby('year_week').agg({
        'pnl': ['sum', 'count', 'mean'],
        'entry_time': 'first'
    }).reset_index()
    weekly_pnl.columns = ['week', 'total_pnl', 'trades', 'avg_pnl', 'first_trade']
    weekly_pnl = weekly_pnl.sort_values('first_trade')
    
    total_weeks = len(weekly_pnl)
    winning_weeks = len(weekly_pnl[weekly_pnl['total_pnl'] > 0])
    losing_weeks = len(weekly_pnl[weekly_pnl['total_pnl'] < 0])
    
    print(f"\n  Summary: {total_weeks} weeks | {winning_weeks} winning | {losing_weeks} losing")
    print(f"  Weekly Win Rate: {winning_weeks/total_weeks:.1%}")
    print(f"  Average Weekly PnL: ${weekly_pnl['total_pnl'].mean():>+8,.2f}")
    print(f"  Best Week: ${weekly_pnl['total_pnl'].max():>+8,.2f}")
    print(f"  Worst Week: ${weekly_pnl['total_pnl'].min():>+8,.2f}")
    print()
    
    for _, week in weekly_pnl.iterrows():
        status = "[WIN]" if week['total_pnl'] > 0 else "[LOSS]" if week['total_pnl'] < 0 else "[B/E]"
        print(f"  {week['week']}  |  {status}  |  "
              f"{week['trades']:2d} trades  |  ${week['total_pnl']:>+8,.2f}  |  "
              f"Avg: ${week['avg_pnl']:>+7,.2f}")
    
    print("\n" + "=" * 70)
    
    # Monthly breakdown
    print("\nPnL BREAKDOWN BY MONTH:")
    print("=" * 70)
    
    monthly_pnl = trades_df.groupby('month').agg({
        'pnl': ['sum', 'count', 'mean'],
        'entry_time': 'first'
    }).reset_index()
    monthly_pnl.columns = ['month', 'total_pnl', 'trades', 'avg_pnl', 'first_trade']
    monthly_pnl = monthly_pnl.sort_values('first_trade')
    
    total_months = len(monthly_pnl)
    winning_months = len(monthly_pnl[monthly_pnl['total_pnl'] > 0])
    losing_months = len(monthly_pnl[monthly_pnl['total_pnl'] < 0])
    
    print(f"\n  Summary: {total_months} months | {winning_months} winning | {losing_months} losing")
    print(f"  Monthly Win Rate: {winning_months/total_months:.1%}")
    print(f"  Average Monthly PnL: ${monthly_pnl['total_pnl'].mean():>+8,.2f}")
    print(f"  Best Month: ${monthly_pnl['total_pnl'].max():>+8,.2f}")
    print(f"  Worst Month: ${monthly_pnl['total_pnl'].min():>+8,.2f}")
    print()
    
    for _, month in monthly_pnl.iterrows():
        status = "[WIN]" if month['total_pnl'] > 0 else "[LOSS]" if month['total_pnl'] < 0 else "[B/E]"
        month_name = pd.to_datetime(month['month'] + '-01').strftime('%B %Y')
        print(f"  {month_name}  |  {status}  |  "
              f"{month['trades']:2d} trades  |  ${month['total_pnl']:>+8,.2f}  |  "
              f"Avg: ${month['avg_pnl']:>+7,.2f}")
    
    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description='Backtest with Yahoo Finance data')
    parser.add_argument('--file', default='data/yahoo/MES_F_20260321.csv',
                       help='CSV file with Yahoo data')
    parser.add_argument('--days', type=int, default=30,
                       help='Number of days to download if no file')
    parser.add_argument('--account', type=float, default=50000,
                       help='Starting account size')
    args = parser.parse_args()
    
    # Check if file exists, if not download it
    data_file = Path(args.file)
    if not data_file.exists():
        print(f"Data file not found: {args.file}")
        print(f"Downloading {args.days} days of MES data...")
        
        # Import and use the yahoo loader
        import subprocess
        result = subprocess.run([
            'py', 'scripts/yahoo_data_loader.py',
            '--symbol', 'MES=F',
            '--period', f'{args.days}d',
            '--interval', '5m',
            '--indicators'
        ], capture_output=True, text=True)
        
        print(result.stdout)
        if result.returncode != 0:
            print("Failed to download data")
            return 1
        
        # Find the most recent MES file after download
        data_dir = Path('data/yahoo')
        if data_dir.exists():
            mes_files = list(data_dir.glob('MES_F_*.csv'))
            if mes_files:
                data_file = max(mes_files, key=lambda x: x.stat().st_mtime)
            else:
                print("No MES data files found after download")
                return 1
        else:
            print("Data directory not found")
            return 1
    
    # Load and run backtest
    df = load_yahoo_data(str(data_file))
    
    if df.empty:
        print("ERROR: No data loaded")
        return 1
    
    results = run_backtest(df, args.account)
    print_report(results)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
