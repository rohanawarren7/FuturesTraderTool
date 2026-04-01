#!/usr/bin/env python3
"""
Extended Data Downloader
=======================

Downloads multiple 60-day chunks from Yahoo Finance and stitches them together
to create 6+ months of historical data for backtesting.

Usage:
    python scripts/download_extended_data.py --months 6
    python scripts/download_extended_data.py --months 9 --symbol MES=F
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
from datetime import datetime, timedelta
import subprocess
import time
import os


def download_chunk(symbol: str, days: int = 60, interval: str = "5m", output_dir: str = "data/yahoo") -> str:
    """
    Download a single 60-day chunk of data.
    Returns the filepath of the downloaded data.
    """
    print(f"\nDownloading {days} days of {symbol} ({interval})...")
    
    # Run the yahoo data loader
    result = subprocess.run([
        'py', 'scripts/yahoo_data_loader.py',
        '--symbol', symbol,
        '--period', f'{days}d',
        '--interval', interval,
        '--indicators'
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"ERROR: Failed to download data")
        print(result.stderr)
        return None
    
    print(result.stdout)
    
    # Find the downloaded file
    data_dir = Path(output_dir)
    symbol_clean = symbol.replace('=', '_')
    files = list(data_dir.glob(f'{symbol_clean}_*.csv'))
    
    if not files:
        print(f"ERROR: No data file found for {symbol}")
        return None
    
    # Return the most recent file
    latest_file = max(files, key=lambda x: x.stat().st_mtime)
    print(f"[OK] Downloaded: {latest_file}")
    return str(latest_file)


def stitch_data(filepaths: list, output_file: str = None) -> pd.DataFrame:
    """
    Stitch multiple data files together, removing duplicates.
    """
    print("\n" + "="*70)
    print("STITCHING DATA FILES")
    print("="*70)
    
    all_data = []
    total_rows = 0
    
    for i, filepath in enumerate(filepaths):
        print(f"\n[{i+1}/{len(filepaths)}] Loading: {filepath}")
        
        if not os.path.exists(filepath):
            print(f"WARNING: File not found: {filepath}")
            continue
        
        df = pd.read_csv(filepath)
        rows = len(df)
        total_rows += rows
        
        # Parse dates
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        
        # Show date range
        start_date = df['timestamp'].min()
        end_date = df['timestamp'].max()
        print(f"       Rows: {rows:,} | Range: {start_date} to {end_date}")
        
        all_data.append(df)
    
    if not all_data:
        print("ERROR: No data to stitch")
        return None
    
    # Concatenate all dataframes
    print("\nConcatenating data...")
    combined = pd.concat(all_data, ignore_index=True)
    print(f"Total rows before dedup: {len(combined):,}")
    
    # Sort by timestamp
    combined = combined.sort_values('timestamp')
    
    # Remove duplicates (keep last occurrence)
    print("Removing duplicates...")
    before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=['timestamp'], keep='last')
    after_dedup = len(combined)
    duplicates = before_dedup - after_dedup
    
    print(f"Duplicates removed: {duplicates:,}")
    print(f"Final row count: {after_dedup:,}")
    
    # Show final date range
    final_start = combined['timestamp'].min()
    final_end = combined['timestamp'].max()
    days_span = (final_end - final_start).days
    
    print(f"\nFinal Date Range: {final_start.strftime('%Y-%m-%d')} to {final_end.strftime('%Y-%m-%d')}")
    print(f"Total Days: {days_span} days")
    print(f"Total Trades Possible: ~{days_span * 6} days (6 sessions/day)")
    
    # Save to file
    if output_file is None:
        symbol_clean = filepaths[0].split('/')[-1].split('_')[0]
        output_file = f"data/yahoo/{symbol_clean}_EXTENDED_{final_start.strftime('%Y%m%d')}_{final_end.strftime('%Y%m%d')}.csv"
    
    combined.to_csv(output_file, index=False)
    print(f"\n[SAVED] {output_file}")
    print(f"File size: {os.path.getsize(output_file) / (1024*1024):.1f} MB")
    
    return combined


def main():
    parser = argparse.ArgumentParser(description='Download extended historical data')
    parser.add_argument('--symbol', default='MES=F', help='Futures symbol (e.g., MES=F, MNQ=F)')
    parser.add_argument('--months', type=int, default=6, help='Number of months to download')
    parser.add_argument('--interval', default='5m', help='Bar interval (5m, 15m, 30m, 1h)')
    parser.add_argument('--chunks', type=int, default=None, help='Number of 60-day chunks (auto-calculated if not specified)')
    
    args = parser.parse_args()
    
    print("="*70)
    print("EXTENDED DATA DOWNLOADER")
    print("="*70)
    print(f"Symbol: {args.symbol}")
    print(f"Target Period: {args.months} months")
    print(f"Interval: {args.interval}")
    
    # Calculate number of chunks needed
    days_per_chunk = 60
    total_days_needed = args.months * 30
    
    if args.chunks:
        num_chunks = args.chunks
    else:
        num_chunks = (total_days_needed // days_per_chunk) + 1
    
    print(f"Chunks to download: {num_chunks} (60 days each)")
    print("="*70)
    
    # Download each chunk
    downloaded_files = []
    
    for i in range(num_chunks):
        print(f"\n{'='*70}")
        print(f"CHUNK {i+1} of {num_chunks}")
        print(f"{'='*70}")
        
        filepath = download_chunk(
            symbol=args.symbol,
            days=days_per_chunk,
            interval=args.interval
        )
        
        if filepath:
            downloaded_files.append(filepath)
        else:
            print(f"WARNING: Failed to download chunk {i+1}")
        
        # Wait between downloads to avoid rate limiting
        if i < num_chunks - 1:
            wait_time = 5
            print(f"\nWaiting {wait_time} seconds before next download...")
            time.sleep(wait_time)
    
    if not downloaded_files:
        print("\nERROR: No data was downloaded successfully")
        return 1
    
    # Stitch all chunks together
    print("\n" + "="*70)
    print("FINALIZING")
    print("="*70)
    
    combined_df = stitch_data(downloaded_files)
    
    if combined_df is None:
        print("ERROR: Failed to stitch data")
        return 1
    
    print("\n" + "="*70)
    print("DOWNLOAD COMPLETE")
    print("="*70)
    print(f"\nYou can now run backtest with:")
    print(f"  py scripts/backtest_yahoo_data.py --file data/yahoo/{args.symbol.replace('=', '_')}_EXTENDED_*.csv")
    print("="*70)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
