"""
VWAPCalculator test suite.

Run:
    pytest tests/test_vwap_calculator.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from core.vwap_calculator import VWAPCalculator


def make_ohlcv(n_bars: int = 100, n_days: int = 2, seed: int = 42) -> pd.DataFrame:
    """Generates synthetic OHLCV data across n_days sessions."""
    rng = np.random.default_rng(seed)
    bars_per_day = n_bars // n_days
    base_price = 5000.0

    rows = []
    for day in range(n_days):
        day_start = datetime(2025, 3, 1 + day, 9, 30)
        price = base_price + rng.uniform(-50, 50)
        for bar in range(bars_per_day):
            ts = day_start + timedelta(minutes=bar * 5)
            change = rng.uniform(-5, 5)
            o = price
            c = price + change
            h = max(o, c) + rng.uniform(0, 3)
            l = min(o, c) - rng.uniform(0, 3)
            v = int(rng.uniform(500, 2000))
            rows.append({"timestamp": ts, "open": o, "high": h,
                          "low": l, "close": c, "volume": v})
            price = c
    return pd.DataFrame(rows)


def test_vwap_columns_present():
    """All VWAP and SD band columns must be present after calculation."""
    df = make_ohlcv(100, 2)
    result = VWAPCalculator.calculate_session_vwap(df)
    expected_cols = [
        "vwap", "vwap_std",
        "vwap_sd1_upper", "vwap_sd1_lower",
        "vwap_sd2_upper", "vwap_sd2_lower",
        "vwap_sd3_upper", "vwap_sd3_lower",
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing column: {col}"


def test_sd_bands_are_symmetric():
    """SD bands should be equidistant above and below VWAP."""
    df = make_ohlcv(100, 1)
    result = VWAPCalculator.calculate_session_vwap(df)
    result = result.dropna()

    for mult in [1, 2, 3]:
        upper_dist = result[f"vwap_sd{mult}_upper"] - result["vwap"]
        lower_dist = result["vwap"] - result[f"vwap_sd{mult}_lower"]
        pd.testing.assert_series_equal(
            upper_dist.reset_index(drop=True),
            lower_dist.reset_index(drop=True),
            check_names=False,
            rtol=1e-9,
        )


def test_vwap_resets_each_day():
    """VWAP must restart at each new session date."""
    df = make_ohlcv(100, 2)
    result = VWAPCalculator.calculate_session_vwap(df)

    # Get the first bar of day 2 (index 50) and last bar of day 1 (index 49)
    first_bar_day2 = result.iloc[50]
    last_bar_day1 = result.iloc[49]

    # On the first bar of a new day, VWAP == typical price of that bar
    tp_day2_bar1 = (df.iloc[50]["high"] + df.iloc[50]["low"] + df.iloc[50]["close"]) / 3
    assert first_bar_day2["vwap"] == pytest.approx(tp_day2_bar1, rel=1e-6)

    # VWAP at last bar of day 1 should NOT equal VWAP at first bar of day 2
    assert last_bar_day1["vwap"] != pytest.approx(first_bar_day2["vwap"], rel=1e-3)


def test_sd_bands_wider_than_vwap():
    """SD2 bands must always be wider than SD1 bands."""
    df = make_ohlcv(200, 2)
    result = VWAPCalculator.calculate_session_vwap(df).dropna()

    assert (result["vwap_sd2_upper"] >= result["vwap_sd1_upper"]).all()
    assert (result["vwap_sd2_lower"] <= result["vwap_sd1_lower"]).all()


def test_vwap_position_classification():
    """get_vwap_position returns correct zone for known values."""
    vwap = 5000.0
    sd1_u, sd1_l = 5010.0, 4990.0
    sd2_u, sd2_l = 5020.0, 4980.0

    assert VWAPCalculator.get_vwap_position(5025, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "ABOVE_SD2"
    assert VWAPCalculator.get_vwap_position(5015, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "ABOVE_SD1"
    assert VWAPCalculator.get_vwap_position(5005, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "ABOVE_VWAP"
    assert VWAPCalculator.get_vwap_position(4995, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "BELOW_VWAP"
    assert VWAPCalculator.get_vwap_position(4985, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "BELOW_SD1"
    assert VWAPCalculator.get_vwap_position(4975, vwap, sd1_u, sd1_l, sd2_u, sd2_l) == "BELOW_SD2"


def test_no_nan_after_warmup():
    """VWAP should have no NaN values after the first bar of each session."""
    df = make_ohlcv(200, 2)
    result = VWAPCalculator.calculate_session_vwap(df)
    assert result["vwap"].isna().sum() == 0


def test_anchored_vwap_weekly():
    """Weekly-anchored VWAP column should be present and non-null after warmup."""
    df = make_ohlcv(200, 4)
    result = VWAPCalculator.calculate_anchored_vwap(df, "weekly")
    assert "vwap_weekly" in result.columns
    assert result["vwap_weekly"].notna().any()
