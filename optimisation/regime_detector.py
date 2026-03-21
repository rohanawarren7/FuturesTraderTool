"""
Market Regime Classifier
Labels each bar as one of: BALANCED, IMBALANCED_BULL, IMBALANCED_BEAR,
VOLATILE_TRANS, LOW_ACTIVITY.

Uses the rule-based MarketStateDetector to auto-label historical data,
then trains a Random Forest classifier so that future bars can be
classified from OHLCV features alone (without needing real-time delta).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from core.market_state_detector import MarketStateDetector
from core.vwap_calculator import VWAPCalculator

# sklearn is part of the standard ML ecosystem — add to requirements if needed
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


REGIME_LABELS = [
    "BALANCED",
    "IMBALANCED_BULL",
    "IMBALANCED_BEAR",
    "VOLATILE_TRANS",
    "LOW_ACTIVITY",
]


class MarketRegimeClassifier:
    """
    Classifies market regime from OHLCV-derived features.

    Two-step approach:
      1. label_historical(df) — uses rule-based MarketStateDetector to label bars
      2. fit(df) — trains a Random Forest on those labels
      3. predict(features) — classifies new bars using the trained model
    """

    def __init__(self):
        self.detector = MarketStateDetector()
        self.model = None
        self.label_encoder = LabelEncoder() if SKLEARN_AVAILABLE else None
        self._is_fitted = False

    # ------------------------------------------------------------------
    # Feature engineering
    # ------------------------------------------------------------------

    @staticmethod
    def _build_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Extracts OHLCV-based regime features from a VWAP-enriched DataFrame.
        df must have been processed by VWAPCalculator.calculate_session_vwap() first.
        """
        f = pd.DataFrame(index=df.index)

        # Price position relative to VWAP bands (normalised)
        vwap_std = df["vwap_std"].replace(0, np.nan)
        f["dist_to_vwap_sd"] = (df["close"] - df["vwap"]) / vwap_std

        # ATR ratio (volatility regime)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_avg = atr.rolling(20).mean()
        f["atr_ratio"] = (atr / atr_avg).fillna(1.0).clip(0, 5)

        # Volume ratio
        vol_avg = df["volume"].rolling(20).mean().replace(0, np.nan)
        f["volume_ratio"] = (df["volume"] / vol_avg).fillna(1.0).clip(0, 10)

        # Delta proxy (close - open, normalised by bar range)
        bar_range = (df["high"] - df["low"]).replace(0, np.nan)
        f["delta_proxy_norm"] = ((df["close"] - df["open"]) / bar_range).fillna(0)

        # VWAP crossing frequency (last 10 bars)
        above_vwap = (df["close"] > df["vwap"]).astype(int)
        f["vwap_crosses_10"] = (
            above_vwap.rolling(10).apply(lambda x: x.diff().abs().sum(), raw=True)
            .fillna(0)
        )

        # 5-bar directional bias (slope proxy)
        f["price_slope_5"] = df["close"].diff(5) / (vwap_std * 5 + 1e-9)

        return f.fillna(0)

    # ------------------------------------------------------------------
    # Auto-labelling
    # ------------------------------------------------------------------

    def label_historical(self, df: pd.DataFrame) -> pd.Series:
        """
        Labels every bar in df using the rule-based MarketStateDetector.
        Returns a Series of regime labels aligned to df's index.
        """
        vwap_std = df["vwap_std"].replace(0, np.nan)
        labels = []

        for i, row in df.iterrows():
            std = vwap_std.loc[i] if not pd.isna(vwap_std.loc[i]) else 1.0

            vwap_pos = VWAPCalculator.get_vwap_position(
                row["close"], row["vwap"],
                row["vwap_sd1_upper"], row["vwap_sd1_lower"],
                row["vwap_sd2_upper"], row["vwap_sd2_lower"],
            )
            delta_proxy = row["close"] - row["open"]
            delta_dir = ("POSITIVE" if delta_proxy > 0
                         else ("NEGATIVE" if delta_proxy < 0 else "NEUTRAL"))

            tr_est = row["high"] - row["low"]
            atr_ratio = 1.0  # default when rolling context unavailable per-row

            label = self.detector.detect(
                vwap_position=vwap_pos,
                cumulative_delta=delta_proxy,
                delta_direction=delta_dir,
                atr_ratio=atr_ratio,
                volume_ratio=1.0,
                price_crosses_vwap_last_10=2,
            )
            labels.append(label)

        return pd.Series(labels, index=df.index, name="regime")

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame):
        """
        Trains a Random Forest on OHLCV-derived features.
        df must have VWAP columns (from VWAPCalculator).
        """
        if not SKLEARN_AVAILABLE:
            print("[RegimeClassifier] scikit-learn not installed — "
                  "using rule-based detection only.")
            return

        print("[RegimeClassifier] Labelling historical bars...")
        labels = self.label_historical(df)

        print("[RegimeClassifier] Building features...")
        features = self._build_features(df)

        mask = features.notna().all(axis=1)
        X = features[mask].values
        y = labels[mask].values

        encoded_y = self.label_encoder.fit_transform(y)

        print(f"[RegimeClassifier] Training Random Forest on {len(X)} bars...")
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=8,
            min_samples_leaf=20,
            n_jobs=-1,
            random_state=42,
        )
        self.model.fit(X, encoded_y)
        self._is_fitted = True

        # Class distribution
        unique, counts = np.unique(y, return_counts=True)
        print("[RegimeClassifier] Training regime distribution:")
        for label, count in zip(unique, counts):
            print(f"  {label}: {count} bars ({count/len(y)*100:.1f}%)")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, df_row: pd.DataFrame) -> str:
        """
        Predicts the regime for a single bar (passed as a one-row DataFrame
        with VWAP columns already computed).
        Falls back to rule-based detection if model not fitted.
        """
        if not self._is_fitted or not SKLEARN_AVAILABLE:
            # Fallback: use rule-based detector
            row = df_row.iloc[0] if hasattr(df_row, "iloc") else df_row
            vwap_pos = VWAPCalculator.get_vwap_position(
                row["close"], row["vwap"],
                row["vwap_sd1_upper"], row["vwap_sd1_lower"],
                row["vwap_sd2_upper"], row["vwap_sd2_lower"],
            )
            delta = row["close"] - row["open"]
            delta_dir = "POSITIVE" if delta > 0 else ("NEGATIVE" if delta < 0 else "NEUTRAL")
            return self.detector.detect(vwap_pos, delta, delta_dir, 1.0, 1.0, 2)

        features = self._build_features(df_row)
        X = features.fillna(0).values
        encoded = self.model.predict(X)[0]
        return self.label_encoder.inverse_transform([encoded])[0]

    def predict_series(self, df: pd.DataFrame) -> pd.Series:
        """Predicts regime for every row in df. Returns a Series."""
        if not self._is_fitted or not SKLEARN_AVAILABLE:
            return self.label_historical(df)

        features = self._build_features(df)
        X = features.fillna(0).values
        encoded = self.model.predict(X)
        labels = self.label_encoder.inverse_transform(encoded)
        return pd.Series(labels, index=df.index, name="regime")
