#!/usr/bin/env python3
"""
Generate Sample MBT Data for Testing - 4-Hour Bars
=====================================================

Creates realistic Micro Bitcoin Futures (MBT) test data with 4-hour bars.
This simulates 6 months of 4H bars with crypto-like volatility.

Usage:
    python crypto_strategy/scripts/generate_mbt_4h.py --days 180 --output crypto_strategy/data/MBT_4h_sample.csv
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import argparse
import os


def generate_mbt_4h_sample(days: int = 180, output_file: str = "crypto_strategy/data/MBT_4h_sample.csv"):
    """
    Generate realistic MBT (Micro Bitcoin Futures) sample data with 4H bars.
    
    Characteristics:
    - 4-hour bars (6 bars per trading day)
    - High volatility (5-15% daily range typical)
    - VWAP behavior (mean reversion tendency)
    - Volume patterns (higher during US hours)
    - Gaps and jumps (crypto-style)
    """
    
    print("="*70)
    print("GENERATING MBT SAMPLE DATA - 4-HOUR BARS")
    print("="*70)
    print(f"Period: {days} days")
    print(f"Bars: 4-hour intervals (6 bars/day)")
    print(f"Output: {output_file}")
    print("="*70 + "\n")
    
    # Calculate number of bars
    # 6 bars per day (4-hour intervals: 18:00, 22:00, 02:00, 06:00, 10:00, 14:00)
    total_bars = days * 6
    
    # Generate timestamps for 4-hour bars
    timestamps = []
    current_date = datetime.now() - timedelta(days=days)
    
    for _ in range(days):
        # Skip to next weekday if weekend
        while current_date.weekday() >= 5:  # Saturday=5, Sunday=6
            current_date += timedelta(days=1)
        
        # Generate 4-hour bars for this day
        # 18:00, 22:00 (same day), then 02:00, 06:00, 10:00, 14:00 (next day)
        bar_hours = [18, 22, 2, 6, 10, 14]
        
        for hour in bar_hours:
            if hour >= 18:
                # Same day
                bar_time = current_date.replace(hour=hour, minute=0, second=0)
            else:
                # Next day
                next_day = current_date + timedelta(days=1)
                bar_time = next_day.replace(hour=hour, minute=0, second=0)
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
        # Volatility for 4-hour bars (less than 15-min but still high)
        hour = timestamps[i].hour
        
        # Higher volatility during US hours (10:00-14:00 and 22:00 overlap)
        if hour in [10, 14, 22]:
            base_volatility = 0.025  # 2.5% per 4H bar
        elif hour in [6, 18]:
            base_volatility = 0.018  # 1.8% 
        else:
            base_volatility = 0.012  # 1.2% overnight
        
        # Add random volatility spikes (crypto characteristic)
        if np.random.random() < 0.08:  # 8% chance of spike per 4H bar
            base_volatility *= 2.5
        
        # Mean reversion component (tendency to return to VWAP)
        if len(prices) > 6:  # Look back 6 bars (1 day of 4H)
            recent_mean = np.mean([p['close'] for p in prices[-6:]])
            distance_from_mean = (current_price - recent_mean) / recent_mean
            
            # Pull back toward mean (stronger mean reversion on 4H)
            mean_reversion = -distance_from_mean * 0.4
        else:
            mean_reversion = 0
        
        # Random walk with mean reversion
        drift = mean_reversion + np.random.normal(0, base_volatility)
        
        # Occasional jumps (crypto news/events)
        if np.random.random() < 0.03:  # 3% chance of jump per 4H bar
            drift += np.random.choice([-1, 1]) * np.random.uniform(0.03, 0.08)
        
        # Calculate OHLC
        open_price = current_price
        close_price = current_price * (1 + drift)
        
        # High and low with intrabar volatility
        intrabar_vol = base_volatility * 1.3
        high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, intrabar_vol)))
        low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, intrabar_vol)))
        
        # Volume (higher during US hours)
        if hour in [10, 14, 22]:
            base_volume = np.random.randint(5000, 15000)
        elif hour in [6, 18]:
            base_volume = np.random.randint(3000, 8000)
        else:
            base_volume = np.random.randint(1000, 4000)
        
        # Volume spikes on large moves
        if abs(drift) > 0.04:
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
    
    # Daily VWAP (6 bars = 1 day of 4H)
    df['vwap'] = (df['typical_price'] * df['volume']).rolling(window=6, min_periods=1).sum() / \
                  df['volume'].rolling(window=6, min_periods=1).sum()
    
    # VWAP standard deviation
    df['vwap_std'] = df['typical_price'].rolling(window=6, min_periods=1).std()
    df['vwap_sd1_upper'] = df['vwap'] + df['vwap_std']
    df['vwap_sd1_lower'] = df['vwap'] - df['vwap_std']
    df['vwap_sd2_upper'] = df['vwap'] + 2 * df['vwap_std']
    df['vwap_sd2_lower'] = df['vwap'] - 2 * df['vwap_std']
    
    # ATR (10 bars for 4H data)
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['prev_close'])
    df['tr3'] = abs(df['low'] - df['prev_close'])
    df['true_range'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['true_range'].rolling(window=10, min_periods=1).mean()
    
    # Volume metrics
    df['volume_avg'] = df['volume'].rolling(window=10, min_periods=1).mean()
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
    print(f"4H volatility: {(output_df['close'].pct_change().std() * np.sqrt(6) * 100):.1f}% daily")
    print(f"File size: {file_size:.1f} MB")
    print(f"\nOK: Saved to: {output_file}\n")
    
    return output_file


def main():
    parser = argparse.ArgumentParser(description='Generate MBT 4H sample data')
    parser.add_argument('--days', type=int, default=180,
                       help='Number of days to generate (default: 180)')
    parser.add_argument('--output', type=str, 
                       default='crypto_strategy/data/MBT_4h_sample.csv',
                       help='Output file path')
    
    args = parser.parse_args()
    
    filepath = generate_mbt_4h_sample(args.days, args.output)
    
    print("="*70)
    print("NEXT: Run backtest")
    print("="*70)
    print(f"python crypto_strategy/scripts/backtest_crypto.py --file {filepath}")
    print("="*70)
    
    return 0


if __name__ == "__main__":
    exit(main())
