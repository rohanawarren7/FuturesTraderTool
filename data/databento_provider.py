"""
Databento OHLCV-1m data provider for MES.
Reads daily CSV files, scales prices (÷1e9), converts timestamps to UTC datetime.
Returns a polars DataFrame sorted by time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import polars as pl

# Default path — override with DATA_DIR env var or constructor arg
DEFAULT_DATA_DIR = Path("/mnt/c/Users/tamar/FuturesTraderTool/1m Data 2yrs")

PRICE_SCALE = 1e9          # Databento fixed-point denominator
RTH_START_CT = 8 * 60 + 30  # 08:30 CT in minutes from midnight
RTH_END_CT   = 15 * 60 + 15  # 15:15 CT


def load_ohlcv(
    data_dir: Optional[Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    rth_only: bool = True,
) -> pl.DataFrame:
    """
    Load all daily OHLCV CSV files from data_dir into a single DataFrame.

    Columns returned:
      ts_event  (datetime, UTC nanoseconds → datetime[us, UTC])
      open, high, low, close  (float, actual price)
      volume    (int)
      date      (date)
      time_ct_minutes  (int, minutes since midnight CT — for RTH filtering)

    Args:
        data_dir:   folder containing glbx-mdp3-YYYYMMDD.ohlcv-1m.csv files
        start_date: 'YYYY-MM-DD' inclusive filter
        end_date:   'YYYY-MM-DD' exclusive filter
        rth_only:   if True, keep only 08:30–15:15 CT (regular trading hours)
    """
    data_dir = data_dir or Path(os.getenv("DATABENTO_DATA_DIR", str(DEFAULT_DATA_DIR)))
    files = sorted(data_dir.glob("glbx-mdp3-*.ohlcv-1m.csv"))

    if not files:
        raise FileNotFoundError(f"No OHLCV files found in {data_dir}")

    frames = []
    for f in files:
        date_str = f.stem.split("-")[2]  # glbx-mdp3-YYYYMMDD
        if start_date and date_str < start_date.replace("-", ""):
            continue
        if end_date and date_str >= end_date.replace("-", ""):
            continue
        df = pl.read_csv(f, schema_overrides={
            "ts_event":      pl.UInt64,
            "open":          pl.Int64,
            "high":          pl.Int64,
            "low":           pl.Int64,
            "close":         pl.Int64,
            "volume":        pl.Int64,
        })
        frames.append(df)

    if not frames:
        raise ValueError(f"No files matched date range {start_date}–{end_date}")

    df = pl.concat(frames)

    df = (
        df
        .with_columns([
            pl.col("ts_event").cast(pl.Datetime("ns", "UTC")).alias("ts_event"),
            (pl.col("open")  / PRICE_SCALE).alias("open"),
            (pl.col("high")  / PRICE_SCALE).alias("high"),
            (pl.col("low")   / PRICE_SCALE).alias("low"),
            (pl.col("close") / PRICE_SCALE).alias("close"),
        ])
        .with_columns([
            pl.col("ts_event").dt.convert_time_zone("America/Chicago")
                .dt.date().alias("date"),
            (
                pl.col("ts_event").dt.convert_time_zone("America/Chicago")
                    .dt.hour().cast(pl.Int32) * 60
                + pl.col("ts_event").dt.convert_time_zone("America/Chicago")
                    .dt.minute().cast(pl.Int32)
            ).alias("time_ct_minutes"),
        ])
        .sort("ts_event")
        .select(["ts_event", "date", "time_ct_minutes",
                 "open", "high", "low", "close", "volume"])
    )

    if rth_only:
        df = df.filter(
            (pl.col("time_ct_minutes") >= RTH_START_CT) &
            (pl.col("time_ct_minutes") < RTH_END_CT)
        )

    return df


def add_daily_vwap(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add VWAP and VWAP standard deviation bands to each bar.
    VWAP resets at the start of each trading day.
    """
    df = df.with_columns([
        (pl.col("close") * pl.col("volume")).alias("_pv"),
        (pl.col("volume")).alias("_v"),
    ])

    df = df.with_columns([
        (pl.col("_pv").cum_sum().over("date") / pl.col("_v").cum_sum().over("date"))
            .alias("vwap"),
    ])

    # Rolling variance for SD bands
    df = df.with_columns([
        ((pl.col("close") - pl.col("vwap")) ** 2 * pl.col("volume"))
            .alias("_var_pv"),
    ])
    df = df.with_columns([
        (pl.col("_var_pv").cum_sum().over("date") / pl.col("_v").cum_sum().over("date"))
            .sqrt()
            .alias("vwap_sd"),
    ])

    df = df.with_columns([
        (pl.col("vwap") + pl.col("vwap_sd")).alias("vwap_plus1"),
        (pl.col("vwap") - pl.col("vwap_sd")).alias("vwap_minus1"),
        (pl.col("vwap") + 2 * pl.col("vwap_sd")).alias("vwap_plus2"),
        (pl.col("vwap") - 2 * pl.col("vwap_sd")).alias("vwap_minus2"),
    ])

    return df.drop(["_pv", "_v", "_var_pv"])
