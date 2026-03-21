#!/usr/bin/env python3
"""
Sierra Chart / Denali Data Bridge
Imports tick and bar data exported from Sierra Chart (Denali feed) into the
trading_analysis.db for use in edge validation and backtesting.

Sierra Chart exports to .scid (tick data) and .dly / .csv (bar data).
This script handles the CSV export format (File → Export Chart Data → CSV).

Usage (from WSL2):
    # Import 5-minute bars from Sierra Chart CSV export
    python scripts/sierra_chart_bridge.py --file /mnt/e/SierraChart/Data/MES_5min.csv

    # Import all CSVs from a folder
    python scripts/sierra_chart_bridge.py --folder /mnt/e/SierraChart/Data/

    # Dry run (print stats without importing)
    python scripts/sierra_chart_bridge.py --file MES_5min.csv --dry-run

Sierra Chart CSV column order (standard export):
    Date, Time, Open, High, Low, Close, Volume, [NumTrades], [BidVolume], [AskVolume]
"""

import argparse
import sys
import csv
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()
import os


# ────────────────────────────────────────────────────────────
# Sierra Chart CSV parser
# ────────────────────────────────────────────────────────────

# Sierra Chart date format in CSV exports
SC_DATE_FORMATS = [
    "%Y/%m/%d",   # 2025/03/21
    "%m/%d/%Y",   # 03/21/2025
    "%Y-%m-%d",   # 2025-03-21
]


def parse_sc_datetime(date_str: str, time_str: str) -> datetime:
    """Parses Sierra Chart date + time strings into a UTC datetime."""
    # Sierra Chart times are in the chart's configured timezone (usually ET)
    # We store as-is and label as ET; conversion to UTC done externally if needed.
    combined = f"{date_str.strip()} {time_str.strip()}"
    for fmt in SC_DATE_FORMATS:
        try:
            return datetime.strptime(combined, fmt + " %H:%M:%S")
        except ValueError:
            try:
                return datetime.strptime(combined, fmt + " %H:%M")
            except ValueError:
                continue
    raise ValueError(f"Cannot parse SC datetime: '{combined}'")


def read_sc_csv(file_path: Path) -> list[dict]:
    """
    Reads a Sierra Chart CSV export and returns a list of OHLCV bar dicts.
    Handles both with and without BidVolume/AskVolume columns.
    """
    bars = []
    with open(file_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        # Detect column positions from header
        if header:
            h = [c.strip().lower() for c in header]
            idx = {
                "date":       h.index("date") if "date" in h else 0,
                "time":       h.index("time") if "time" in h else 1,
                "open":       h.index("open") if "open" in h else 2,
                "high":       h.index("high") if "high" in h else 3,
                "low":        h.index("low") if "low" in h else 4,
                "close":      h.index("close") if "close" in h else 5,
                "volume":     h.index("volume") if "volume" in h else 6,
                "bid_vol":    h.index("bidvolume") if "bidvolume" in h else None,
                "ask_vol":    h.index("askvolume") if "askvolume" in h else None,
            }
        else:
            idx = {"date": 0, "time": 1, "open": 2, "high": 3,
                   "low": 4, "close": 5, "volume": 6,
                   "bid_vol": None, "ask_vol": None}

        for row in reader:
            if not row or not row[0].strip():
                continue
            try:
                ts = parse_sc_datetime(row[idx["date"]], row[idx["time"]])
                bid_vol = (float(row[idx["bid_vol"]])
                           if idx["bid_vol"] is not None and len(row) > idx["bid_vol"]
                           else None)
                ask_vol = (float(row[idx["ask_vol"]])
                           if idx["ask_vol"] is not None and len(row) > idx["ask_vol"]
                           else None)

                # Delta proxy from bid/ask volume if available
                delta = None
                if bid_vol is not None and ask_vol is not None:
                    delta = ask_vol - bid_vol  # positive = net buying pressure

                bars.append({
                    "timestamp":  ts.isoformat(),
                    "open":       float(row[idx["open"]]),
                    "high":       float(row[idx["high"]]),
                    "low":        float(row[idx["low"]]),
                    "close":      float(row[idx["close"]]),
                    "volume":     float(row[idx["volume"]]),
                    "bid_volume": bid_vol,
                    "ask_volume": ask_vol,
                    "delta":      delta,
                })
            except (ValueError, IndexError) as e:
                print(f"  [WARN] Skipping malformed row: {row[:3]} — {e}")
                continue

    return bars


def import_to_db(bars: list[dict], symbol: str, db_path: str,
                 timeframe: str = "5m") -> int:
    """
    Inserts bars into the sierra_chart_bars table.
    Creates the table if it doesn't exist.
    Returns number of rows inserted.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sierra_chart_bars (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            bid_volume  REAL,
            ask_volume  REAL,
            delta       REAL,
            UNIQUE(symbol, timeframe, timestamp)
        )
    """)

    inserted = 0
    for bar in bars:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO sierra_chart_bars
                (symbol, timeframe, timestamp, open, high, low, close,
                 volume, bid_volume, ask_volume, delta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, timeframe, bar["timestamp"],
                bar["open"], bar["high"], bar["low"], bar["close"],
                bar["volume"], bar["bid_volume"], bar["ask_volume"], bar["delta"],
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"  [WARN] DB insert error: {e}")

    conn.commit()
    conn.close()
    return inserted


def infer_symbol_from_filename(path: Path) -> str:
    """Extracts instrument symbol from filename. e.g. 'MES_5min.csv' → 'MES'"""
    stem = path.stem.upper()
    for sym in ("MES", "MNQ", "ES", "NQ", "CL", "GC", "NKD"):
        if sym in stem:
            return sym
    return stem.split("_")[0]


def infer_timeframe_from_filename(path: Path) -> str:
    """Extracts timeframe from filename. e.g. 'MES_5min.csv' → '5m'"""
    stem = path.stem.lower()
    for tf in ("1min", "2min", "3min", "5min", "10min", "15min", "30min",
               "1h", "4h", "1day"):
        if tf in stem:
            return tf.replace("min", "m").replace("day", "d")
    return "5m"


def main():
    parser = argparse.ArgumentParser(description="Import Sierra Chart CSV data")
    parser.add_argument("--file", type=str, default=None,
                        help="Path to a single Sierra Chart CSV export file")
    parser.add_argument("--folder", type=str, default=None,
                        help="Import all CSVs in this folder")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Override instrument symbol (default: inferred from filename)")
    parser.add_argument("--timeframe", type=str, default=None,
                        help="Override timeframe (default: inferred from filename)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and count bars without writing to DB")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")

    files = []
    if args.file:
        files = [Path(args.file)]
    elif args.folder:
        files = list(Path(args.folder).glob("*.csv"))
    else:
        print("[ERROR] Specify --file or --folder")
        sys.exit(1)

    total_inserted = 0
    for f in sorted(files):
        symbol    = args.symbol    or infer_symbol_from_filename(f)
        timeframe = args.timeframe or infer_timeframe_from_filename(f)
        print(f"\n[Bridge] {f.name}  →  {symbol} {timeframe}")

        bars = read_sc_csv(f)
        print(f"  Parsed {len(bars)} bars  |  "
              f"Range: {bars[0]['timestamp'][:10] if bars else 'n/a'} "
              f"→ {bars[-1]['timestamp'][:10] if bars else 'n/a'}")

        has_delta = sum(1 for b in bars if b.get("delta") is not None)
        if has_delta:
            print(f"  Real delta available in {has_delta}/{len(bars)} bars")
        else:
            print(f"  [NOTE] No bid/ask volume in export — delta column will be NULL")

        if not args.dry_run:
            n = import_to_db(bars, symbol, db_path, timeframe)
            print(f"  Inserted {n} new bars into sierra_chart_bars")
            total_inserted += n
        else:
            print(f"  [DRY-RUN] Would insert up to {len(bars)} bars")

    if not args.dry_run:
        print(f"\n[Bridge] Total inserted: {total_inserted} bars across {len(files)} file(s)")


if __name__ == "__main__":
    main()
