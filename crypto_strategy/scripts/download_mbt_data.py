#!/usr/bin/env python3
"""
Download MBT Data from IBKR
===========================

Downloads Micro Bitcoin Futures (MBT) historical data from Interactive Brokers.
Requires active IBKR connection and CME futures data subscription.

Usage:
    python crypto_strategy/scripts/download_mbt_data.py --months 3
    python crypto_strategy/scripts/download_mbt_data.py --days 90 --interval 15m
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
from datetime import datetime, timedelta
import os


def download_mbt_from_ibkr(duration: str = "3 M", bar_size: str = "15 mins") -> pd.DataFrame:
    """
    Download MBT historical data from IBKR.
    
    Args:
        duration: "3 M" for 3 months, "6 M" for 6 months, etc.
        bar_size: "5 mins", "15 mins", "1 hour", etc.
    
    Returns:
        DataFrame with OHLCV data
    """
    print("="*70)
    print("DOWNLOADING MBT DATA FROM IBKR")
    print("="*70)
    print(f"Symbol: MBT (Micro Bitcoin Futures)")
    print(f"Duration: {duration}")
    print(f"Bar Size: {bar_size}")
    print("="*70 + "\n")
    
    try:
        from data.ibkr_provider import IBKRDataProvider
        
        provider = IBKRDataProvider()
        
        print("Connecting to IBKR...")
        # Note: Requires TWS/IB Gateway running
        
        print(f"Requesting historical data for MBT...")
        df = provider.get_historical_data(
            symbol="MBT",
            duration=duration,
            bar_size=bar_size,
            use_rth=False  # Include all hours (crypto trades almost 24/7)
        )
        
        if df is None or len(df) == 0:
            print("ERROR: No data received from IBKR")
            print("\nTroubleshooting:")
            print("1. Is TWS/IB Gateway running?")
            print("2. Do you have CME futures data subscription?")
            print("3. Check Account Management > Market Data Subscriptions")
            return None
        
        print(f"✓ Downloaded {len(df)} bars")
        print(f"Date range: {df.index.min()} to {df.index.max()}")
        
        return df
        
    except ImportError:
        print("ERROR: Could not import IBKR provider")
        print("Make sure data/ibkr_provider.py exists")
        return None
    except Exception as e:
        print(f"ERROR: {e}")
        print("\nMake sure:")
        print("- TWS or IB Gateway is running")
        print("- API connections are enabled in TWS")
        print("- You have CME futures market data subscription")
        return None


def save_data(df: pd.DataFrame, output_dir: str = "crypto_strategy/data") -> str:
    """Save downloaded data to CSV."""
    # Create directory if needed
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate filename
    start_date = df.index.min().strftime('%Y%m%d')
    end_date = df.index.max().strftime('%Y%m%d')
    filename = f"MBT_{start_date}_{end_date}.csv"
    filepath = os.path.join(output_dir, filename)
    
    # Save
    df.to_csv(filepath)
    file_size = os.path.getsize(filepath) / (1024*1024)  # MB
    
    print(f"\n✓ Saved to: {filepath}")
    print(f"  File size: {file_size:.1f} MB")
    print(f"  Rows: {len(df):,}")
    
    return filepath


def validate_data(df: pd.DataFrame) -> bool:
    """Validate downloaded data quality."""
    print("\nValidating data...")
    
    issues = []
    
    # Check for missing values
    if df.isnull().sum().sum() > 0:
        issues.append(f"Found {df.isnull().sum().sum()} missing values")
    
    # Check for zero volume bars
    zero_vol = (df['volume'] == 0).sum()
    if zero_vol > len(df) * 0.1:  # More than 10%
        issues.append(f"{zero_vol} bars with zero volume ({zero_vol/len(df)*100:.1f}%)")
    
    # Check for price gaps
    df['prev_close'] = df['close'].shift(1)
    df['gap'] = abs(df['open'] - df['prev_close']) / df['prev_close']
    large_gaps = (df['gap'] > 0.05).sum()  # 5% gaps
    if large_gaps > 10:
        issues.append(f"{large_gaps} large gaps (>5%)")
    
    # Check date continuity
    df['time_diff'] = df.index.to_series().diff()
    expected_diff = pd.Timedelta(minutes=15)  # For 15-min bars
    large_gaps_time = (df['time_diff'] > expected_diff * 2).sum()
    if large_gaps_time > 5:
        issues.append(f"{large_gaps_time} time gaps > 30 minutes")
    
    if issues:
        print("⚠ Issues found:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nData is usable but review recommended")
        return False
    else:
        print("✓ Data validation passed")
        return True


def main():
    parser = argparse.ArgumentParser(description='Download MBT data from IBKR')
    parser.add_argument('--months', type=int, default=3,
                       help='Number of months to download (default: 3)')
    parser.add_argument('--days', type=int, default=None,
                       help='Number of days (overrides months)')
    parser.add_argument('--interval', type=str, default='15m',
                       help='Bar interval: 5m, 15m, 1h (default: 15m)')
    parser.add_argument('--output', type=str, default='crypto_strategy/data',
                       help='Output directory')
    
    args = parser.parse_args()
    
    # Determine duration
    if args.days:
        duration = f"{args.days} D"
    else:
        duration = f"{args.months} M"
    
    # Map interval to IBKR format
    interval_map = {
        '5m': '5 mins',
        '15m': '15 mins',
        '30m': '30 mins',
        '1h': '1 hour'
    }
    bar_size = interval_map.get(args.interval, '15 mins')
    
    print("\n" + "="*70)
    print("MBT DATA DOWNLOADER")
    print("="*70)
    print(f"This will download Micro Bitcoin Futures data from IBKR")
    print(f"Requires: CME Futures Market Data subscription")
    print(f"Cost: $10-25/month (waived with $30+ commissions)")
    print("="*70 + "\n")
    
    # Download data
    df = download_mbt_from_ibkr(duration, bar_size)
    
    if df is None:
        print("\n❌ Download failed")
        return 1
    
    # Validate
    validate_data(df)
    
    # Save
    filepath = save_data(df, args.output)
    
    print("\n" + "="*70)
    print("NEXT STEPS:")
    print("="*70)
    print(f"1. Run backtest:")
    print(f"   python crypto_strategy/scripts/backtest_crypto.py --file {filepath}")
    print(f"\n2. Or copy to main data folder:")
    print(f"   cp {filepath} data/ibkr/")
    print("="*70 + "\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
