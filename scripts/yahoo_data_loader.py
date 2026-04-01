"""
Yahoo Finance Data Loader
=========================

Downloads free intraday futures data from Yahoo Finance.
Works immediately without API keys or accounts.

Usage:
    python scripts/yahoo_data_loader.py --symbol ES=F --period 1mo --interval 5m
    python scripts/yahoo_data_loader.py --symbol NQ=F --period 60d --interval 1m
    python scripts/yahoo_data_loader.py --list-symbols
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Available symbols
FUTURES_SYMBOLS = {
    "ES=F": "E-mini S&P 500 (Full contract)",
    "MES=F": "Micro E-mini S&P 500",
    "NQ=F": "E-mini Nasdaq-100",
    "MNQ=F": "Micro E-mini Nasdaq-100",
    "YM=F": "E-mini Dow ($5)",
    "MYM=F": "Micro E-mini Dow",
    "RTY=F": "E-mini Russell 2000",
    "M2K=F": "Micro E-mini Russell 2000",
    "CL=F": "Crude Oil WTI",
    "GC=F": "Gold",
    "SI=F": "Silver",
    "ZN=F": "10-Year T-Note",
}


def download_yahoo_data(symbol: str, period: str = "1mo", interval: str = "5m") -> pd.DataFrame:
    """
    Download futures data from Yahoo Finance.
    
    Args:
        symbol: Yahoo Finance symbol (e.g., "ES=F", "MES=F")
        period: Time period ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
        interval: Bar size ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo")
        
    Returns:
        DataFrame with OHLCV data
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return pd.DataFrame()
    
    logger.info(f"Downloading {symbol} ({interval} bars, {period})...")
    
    try:
        # Download data
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        
        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return pd.DataFrame()
        
        # Reset index to make datetime a column
        df = df.reset_index()
        
        # Rename columns to standard format
        df.columns = [col.lower().replace(' ', '_') for col in df.columns]
        
        # Rename 'datetime' or 'date' to 'timestamp'
        if 'datetime' in df.columns:
            df = df.rename(columns={'datetime': 'timestamp'})
        elif 'date' in df.columns:
            df = df.rename(columns={'date': 'timestamp'})
        
        # Ensure timestamp is datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Round prices to 2 decimals
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col in df.columns:
                df[col] = df[col].round(2)
        
        # Convert volume to int
        if 'volume' in df.columns:
            df['volume'] = df['volume'].astype(int)
        
        logger.info(f"Downloaded {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")
        
        return df
        
    except Exception as e:
        logger.error(f"Error downloading {symbol}: {e}")
        return pd.DataFrame()


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add technical indicators needed for VWAP strategy.
    """
    from core.vwap_calculator import VWAPCalculator
    
    logger.info("Adding technical indicators...")
    
    # Calculate VWAP
    vwap_calc = VWAPCalculator()
    df = vwap_calc.calculate_session_vwap(df)
    
    # Calculate ATR
    df['atr'] = calculate_atr(df)
    
    # Volume ratio
    df['volume_avg'] = df['volume'].rolling(20).mean()
    df['volume_ratio'] = df['volume'] / df['volume_avg']
    
    # Delta (close - open) as proxy for order flow
    df['delta'] = df['close'] - df['open']
    df['delta_direction'] = df['delta'].apply(
        lambda x: 'POSITIVE' if x > 0 else ('NEGATIVE' if x < 0 else 'NEUTRAL')
    )
    df['delta_flip'] = (
        (df['delta'] > 0) & (df['delta'].shift(1) <= 0) |
        (df['delta'] < 0) & (df['delta'].shift(1) >= 0)
    )
    
    # VWAP position
    df['vwap_position'] = df.apply(get_vwap_position, axis=1)
    
    # Market state
    df['market_state'] = df.apply(detect_market_state, axis=1)
    
    # Time in session (minutes from 9:30 ET)
    df['time_in_session'] = df['timestamp'].apply(time_in_session)
    
    return df


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift(1))
    low_close = abs(df['low'] - df['close'].shift(1))
    
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    
    return atr


def get_vwap_position(row) -> str:
    """Determine price position relative to VWAP bands."""
    close = row['close']
    vwap = row.get('vwap', close)
    sd1_upper = row.get('vwap_sd1_upper', vwap)
    sd1_lower = row.get('vwap_sd1_lower', vwap)
    sd2_upper = row.get('vwap_sd2_upper', vwap + (sd1_upper - vwap) * 2) if not pd.isna(sd1_upper) else vwap + 10
    sd2_lower = row.get('vwap_sd2_lower', vwap - (vwap - sd1_lower) * 2) if not pd.isna(sd1_lower) else vwap - 10
    
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


def detect_market_state(row) -> str:
    """Detect market regime."""
    vwap_pos = row.get('vwap_position', 'INSIDE_SD1')
    close = row['close']
    vwap = row.get('vwap', close)
    atr = row.get('atr', 7.5)
    atr_avg = 7.5  # Approximate
    
    # Determine based on VWAP position and ATR
    if vwap_pos == 'ABOVE_SD1' and close > vwap:
        return "IMBALANCED_BULL"
    elif vwap_pos == 'BELOW_SD1' and close < vwap:
        return "IMBALANCED_BEAR"
    elif atr > atr_avg * 1.5:
        return "VOLATILE_TRANS"
    elif atr < atr_avg * 0.5:
        return "LOW_ACTIVITY"
    else:
        return "BALANCED"


def time_in_session(timestamp) -> int:
    """Calculate minutes since 9:30 AM ET."""
    if isinstance(timestamp, str):
        timestamp = pd.to_datetime(timestamp)
    
    # Convert to ET (UTC-5)
    hour = timestamp.hour - 5
    minute = timestamp.minute
    
    # Minutes from 9:30
    minutes = (hour - 9) * 60 + (minute - 30)
    return max(0, minutes)


def save_data(df: pd.DataFrame, symbol: str, output_dir: str = "./data/yahoo"):
    """Save data to CSV and Parquet."""
    from pathlib import Path
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d")
    filename_base = f"{symbol.replace('=', '_')}_{timestamp}"
    
    # Save as CSV
    csv_path = output_path / f"{filename_base}.csv"
    df.to_csv(csv_path, index=False)
    logger.info(f"Saved CSV: {csv_path}")
    
    # Save as Parquet (more efficient)
    try:
        parquet_path = output_path / f"{filename_base}.parquet"
        df.to_parquet(parquet_path, index=False)
        logger.info(f"Saved Parquet: {parquet_path}")
    except Exception as e:
        logger.warning(f"Could not save Parquet: {e}")
    
    return str(csv_path)


def load_data(symbol: str, data_dir: str = "./data/yahoo") -> pd.DataFrame:
    """Load previously saved data."""
    from pathlib import Path
    
    data_path = Path(data_dir)
    if not data_path.exists():
        return pd.DataFrame()
    
    # Find most recent file
    pattern = f"{symbol.replace('=', '_')}_*.csv"
    files = list(data_path.glob(pattern))
    
    if not files:
        return pd.DataFrame()
    
    # Load most recent
    latest = max(files, key=lambda x: x.stat().st_mtime)
    logger.info(f"Loading {latest}")
    
    return pd.read_csv(latest, parse_dates=['timestamp'])


def main():
    parser = argparse.ArgumentParser(description='Download futures data from Yahoo Finance')
    parser.add_argument('--symbol', default='ES=F', help='Symbol to download (default: ES=F)')
    parser.add_argument('--period', default='1mo', help='Time period (default: 1mo)')
    parser.add_argument('--interval', default='5m', help='Bar interval (default: 5m)')
    parser.add_argument('--indicators', action='store_true', help='Add technical indicators')
    parser.add_argument('--output', default='./data/yahoo', help='Output directory')
    parser.add_argument('--list-symbols', action='store_true', help='List available symbols')
    
    args = parser.parse_args()
    
    if args.list_symbols:
        print("\nAvailable Futures Symbols:")
        print("=" * 60)
        for symbol, description in FUTURES_SYMBOLS.items():
            print(f"  {symbol:10s} - {description}")
        print()
        return 0
    
    # Download data
    df = download_yahoo_data(args.symbol, args.period, args.interval)
    
    if df.empty:
        print(f"ERROR: No data downloaded for {args.symbol}")
        return 1
    
    # Add indicators if requested
    if args.indicators:
        df = add_technical_indicators(df)
    
    # Save data
    save_path = save_data(df, args.symbol, args.output)
    
    # Print summary
    print("\n" + "=" * 70)
    print("DOWNLOAD COMPLETE".center(70))
    print("=" * 70)
    print(f"\nSymbol:        {args.symbol}")
    print(f"Period:        {args.period}")
    print(f"Interval:      {args.interval}")
    print(f"Bars:          {len(df)}")
    print(f"Date Range:    {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Saved to:      {save_path}")
    print("\n" + "=" * 70)
    print("\nSample data:")
    print(df.head(10).to_string())
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
