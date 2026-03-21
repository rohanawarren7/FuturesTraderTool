import pandas as pd
import numpy as np


class VWAPCalculator:
    """
    Calculates multi-timeframe VWAP with standard deviation bands.
    Supports session-anchored, weekly-anchored, and monthly-anchored VWAP.
    """

    @staticmethod
    def calculate_session_vwap(df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates session (daily) VWAP with SD1, SD2, SD3 bands.

        df must have columns: timestamp, open, high, low, close, volume
        Returns df with added columns:
            vwap, vwap_std,
            vwap_sd1_upper, vwap_sd1_lower,
            vwap_sd2_upper, vwap_sd2_lower,
            vwap_sd3_upper, vwap_sd3_lower
        """
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = df["timestamp"].dt.date

        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_volume"] = df["typical_price"] * df["volume"]
        df["tp_vol_sq"] = (df["typical_price"] ** 2) * df["volume"]

        # Cumulative sums reset at each new session date
        df["cum_tp_vol"] = df.groupby("date")["tp_volume"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["cum_tp_vol_sq"] = df.groupby("date")["tp_vol_sq"].cumsum()

        # Avoid division by zero on zero-volume bars
        safe_vol = df["cum_vol"].replace(0, np.nan)
        df["vwap"] = df["cum_tp_vol"] / safe_vol

        # Variance: E[X²] - E[X]²  (numerically stable)
        df["variance"] = (df["cum_tp_vol_sq"] / safe_vol) - (df["vwap"] ** 2)
        df["variance"] = df["variance"].clip(lower=0)
        df["vwap_std"] = np.sqrt(df["variance"])

        for mult in [1, 2, 3]:
            df[f"vwap_sd{mult}_upper"] = df["vwap"] + (mult * df["vwap_std"])
            df[f"vwap_sd{mult}_lower"] = df["vwap"] - (mult * df["vwap_std"])

        # Drop intermediates
        df.drop(
            columns=["typical_price", "tp_volume", "tp_vol_sq",
                     "cum_tp_vol", "cum_vol", "cum_tp_vol_sq", "variance"],
            inplace=True,
        )
        return df

    @staticmethod
    def calculate_anchored_vwap(
        df: pd.DataFrame, anchor_type: str = "weekly"
    ) -> pd.DataFrame:
        """
        Calculates VWAP anchored to the start of the week or month.
        anchor_type: 'weekly' or 'monthly'
        Adds column: vwap_weekly or vwap_monthly
        """
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_volume"] = df["typical_price"] * df["volume"]

        if anchor_type == "weekly":
            df["anchor_key"] = df["timestamp"].dt.to_period("W")
        elif anchor_type == "monthly":
            df["anchor_key"] = df["timestamp"].dt.to_period("M")
        else:
            raise ValueError(f"anchor_type must be 'weekly' or 'monthly', got {anchor_type!r}")

        df["cum_tp_vol"] = df.groupby("anchor_key")["tp_volume"].cumsum()
        df["cum_vol"] = df.groupby("anchor_key")["volume"].cumsum()

        safe_vol = df["cum_vol"].replace(0, np.nan)
        df[f"vwap_{anchor_type}"] = df["cum_tp_vol"] / safe_vol

        df.drop(
            columns=["typical_price", "tp_volume", "anchor_key",
                     "cum_tp_vol", "cum_vol"],
            inplace=True,
        )
        return df

    @staticmethod
    def get_vwap_position(
        price: float,
        vwap: float,
        sd1_u: float,
        sd1_l: float,
        sd2_u: float,
        sd2_l: float,
    ) -> str:
        """
        Returns the price's position relative to VWAP bands.
        Used as the primary feature for balance/imbalance detection.
        """
        if price > sd2_u:
            return "ABOVE_SD2"
        elif price > sd1_u:
            return "ABOVE_SD1"
        elif price >= vwap:
            return "ABOVE_VWAP"
        elif price >= sd1_l:
            return "BELOW_VWAP"
        elif price >= sd2_l:
            return "BELOW_SD1"
        else:
            return "BELOW_SD2"
