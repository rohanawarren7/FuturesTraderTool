#!/usr/bin/env python3
"""
Generate Sample MBT Data for Testing
=====================================

Creates realistic Micro Bitcoin Futures (MBT) test data for strategy development.
This simulates 3 months of 15-minute bars with crypto-like volatility.

Usage:
    python crypto_strategy/scripts/generate_mbt_sample.py --days 90 --output crypto_strategy/data/MBT_sample.csv
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import argparse
import os


def generate_mbt_sample(days: int = 90, output_file: str = "crypto_strategy/data/MBT_sample.csv"):
    """
    Generate realistic MBT (Micro Bitcoin Futures) sample data.
    
    Characteristics:
    - 15-minute bars
    - High volatility (5-15% daily range typical)
    - VWAP behavior (mean reversion tendency)
    - Volume patterns (higher during US hours)
    - Gaps and jumps (crypto-style)
    """
    
    print("="*70)
    print("GENERATING MBT SAMPLE DATA")
    print("="*70)
    print(f"Period: {days} days")
    print(f"Bars: 15-minute intervals")
    print(f"Output: {output_file}")
    print("="*70 + "\n")
    
    # Calculate number of bars
    # 23 hours/day * 4 bars/hour = 92 bars/day
    # But we'll generate slightly fewer to account for gaps
    total_bars = days * 92
    
    # Generate timestamps (exclude weekends partially)
    timestamps = []
    current_date = datetime.now() - timedelta(days=days)
    
    for _ in range(days):
        # Skip to next weekday if weekend
        while current_date.weekday() >= 5:  # Saturday=5, Sunday=6
            current_date += timedelta(days=1)
        
        # Generate bars for this day (18:00 to 17:00 next day)
        day_start = current_date.replace(hour=18, minute=0, second=0)
        
        for bar in range(92):  # 23 hours * 4 bars
            bar_time = day_start + timedelta(minutes=15*bar)
            timestamps.append(bar_time)
        
        current_date += timedelta(days=1)
    
    # Limit to requested bars
    timestamps = timestamps[:total_bars]
    
    # Starting price (MBT around $600-800 per 0.1 BTC in recent times)
    start_price = 650.0
    
    # Generate price series with crypto characteristics
    prices = []
    current_price = start_price
    
    for i in range(len(timestamps)):
        # Volatility varies by time of day
        hour = timestamps[i].hour
        
        # Higher volatility during US hours (09:30-16:00)
        if 9 <= hour < 16:
            base_volatility = 0.008  # 0.8% per 15-min bar
        elif 19 <= hour < 23:
            base_volatility = 0.006  # 0.6% evening
        else:
            base_volatility = 0.004  # 0.4% overnight
        
        # Add random volatility spikes (crypto characteristic)
        if np.random.random() < 0.05:  # 5% chance of spike
            base_volatility *= 3.0
        
        # Mean reversion component (tendency to return to VWAP)
        if len(prices) > 20:
            recent_mean = np.mean([p['close'] for p in prices[-20:]])
            distance_from_mean = (current_price - recent_mean) / recent_mean
            
            # Pull back toward mean (mean reversion)
            mean_reversion = -distance_from_mean * 0.3
        else:
            mean_reversion = 0
        
        # Random walk with mean reversion
        drift = mean_reversion + np.random.normal(0, base_volatility)
        
        # Occasional jumps (crypto news/events)
        if np.random.random() < 0.01:  # 1% chance of jump
            drift += np.random.choice([-1, 1]) * np.random.uniform(0.02, 0.05)
        
        # Calculate OHLC
        open_price = current_price
        close_price = current_price * (1 + drift)
        
        # High and low with intrabar volatility
        intrabar_vol = base_volatility * 1.5
        high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, intrabar_vol)))
        low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, intrabar_vol)))
        
        # Volume (higher during US hours)
        if 9 <= hour < 16:
            base_volume = np.random.randint(2000, 8000)
        elif 19 <= hour < 23:
            base_volume = np.random.randint(1000, 4000)
        else:
            base_volume = np.random.randint(200, 1500)
        
        # Volume spikes on large moves
        if abs(drift) > 0.02:
            base_volume = int(base_volume * np.random.uniform(1.5, 3.0))
        
        prices.append({
            'timestamp': timestamps[i],
            'open': round(open_price, 2),
            'high': round(high_price, 2),
            'low': round(low_price, 2),
            'close': round(close_price, 2),
            'volume': base_volume
        })
        
        current_price = close_price
    
    # Create DataFrame
    df = pd.DataFrame(prices)
    
    # Calculate VWAP and indicators (for consistency)
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    
    # 4-hour rolling VWAP
    df['vwap'] = (df['typical_price'] * df['volume']).rolling(window=16, min_periods=1).sum() / \
                  df['volume'].rolling(window=16, min_periods=1).sum()
    
    # VWAP standard deviation
    df['vwap_std'] = df['typical_price'].rolling(window=16, min_periods=1).std()
    df['vwap_sd1_upper'] = df['vwap'] + df['vwap_std']
    df['vwap_sd1_lower'] = df['vwap'] - df['vwap_std']
    df['vwap_sd2_upper'] = df['vwap'] + 2 * df['vwap_std']
    df['vwap_sd2_lower'] = df['vwap'] - 2 * df['vwap_std']
    
    # ATR (20 bars)
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['prev_close'])
    df['tr3'] = abs(df['low'] - df['prev_close'])
    df['true_range'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['true_range'].rolling(window=20, min_periods=1).mean()
    
    # Volume metrics
    df['volume_avg'] = df['volume'].rolling(window=20, min_periods=1).mean()
    df['volume_ratio'] = df['volume'] / df['volume_avg']
    
    # Delta proxy
    df['delta'] = df['close'] - df['open']
    df['delta_direction'] = np.where(df['delta'] > 0, 'POSITIVE',
                                     np.where(df['delta'] < 0, 'NEGATIVE', 'NEUTRAL'))
    df['delta_flip'] = df['delta_direction'] != df['delta_direction'].shift(1)
    
    # Select columns to match expected format
    output_df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume',
                   'vwap', 'vwap_std', 'vwap_sd1_upper', 'vwap_sd1_lower',
                   'vwap_sd2_upper', 'vwap_sd2_lower', 'atr',
                   'volume_avg', 'volume_ratio', 'delta', 'delta_direction', 'delta_flip']].copy()
    
    # Save to CSV
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    output_df.to_csv(output_file, index=False)
    
    file_size = os.path.getsize(output_file) / (1024*1024)
    
    print(f"OK: Generated {len(output_df)} bars")
    print(f"Date range: {output_df['timestamp'].min()} to {output_df['timestamp'].max()}")
    print(f"Price range: ${output_df['close'].min():.2f} - ${output_df['close'].max():.2f}")
    print(f"Daily volatility: {(output_df['close'].pct_change().std() * np.sqrt(92) * 100):.1f}%")
    print(f"File size: {file_size:.1f} MB")
    print(f"\nOK: Saved to: {output_file}\n")
    
    return output_file


def main():
    parser = argparse.ArgumentParser(description='Generate MBT sample data')
    parser.add_argument('--days', type=int, default=90,
                       help='Number of days to generate (default: 90)')
    parser.add_argument('--output', type=str, 
                       default='crypto_strategy/data/MBT_sample.csv',
                       help='Output file path')
    
    args = parser.parse_args()
    
    filepath = generate_mbt_sample(args.days, args.output)
    
    print("="*70)
    print("NEXT: Run backtest")
    print("="*70)
    print(f"python crypto_strategy/scripts/backtest_crypto.py --file {filepath}")
    print("="*70)
    
    return 0


if __name__ == "__main__":
    exit(main())
