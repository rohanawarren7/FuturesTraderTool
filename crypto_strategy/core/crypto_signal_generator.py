"""
Crypto Signal Generator
=======================

VWAP-based mean reversion signals for cryptocurrency futures (MBT).
Adapted for crypto volatility and market structure.

Completely separate from MES signal generator.
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime

# Import crypto-specific configs
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.crypto_instrument_specs import CRYPTO_SESSION_CONFIG


class CryptoSignalGenerator:
    """
    Signal generator for Micro Bitcoin Futures (MBT).
    
    Key differences from MES:
    - 15-minute bars (not 5-min)
    - 4-hour rolling VWAP (not session-based)
    - Wider stops (2.5x ATR vs 1.5x)
    - Different time filters (crypto-specific sessions)
    - Lower confidence thresholds
    """
    
    def __init__(self, 
                 use_conservative_mode: bool = True,
                 volatility_regime: str = "NORMAL"):
        """
        Initialize crypto signal generator.
        
        Args:
            use_conservative_mode: If True, reduces size by 50%
            volatility_regime: NORMAL, HIGH, or EXTREME
        """
        self.use_conservative_mode = use_conservative_mode
        self.volatility_regime = volatility_regime
        self.session_config = CRYPTO_SESSION_CONFIG
        
        # Adjust thresholds based on volatility
        self.confidence_threshold = self._get_confidence_threshold()
        
    def _get_confidence_threshold(self) -> float:
        """Get confidence threshold based on volatility regime."""
        thresholds = {
            "NORMAL": 0.45,
            "HIGH": 0.50,
            "EXTREME": 0.55
        }
        return thresholds.get(self.volatility_regime, 0.45)
    
    def generate(self,
                 bar_data: pd.Series,
                 timestamp: datetime,
                 vwap_data: Dict,
                 delta_metrics: Dict) -> Dict:
        """
        Generate trading signal for crypto.
        
        Args:
            bar_data: Current bar OHLCV
            timestamp: Current timestamp
            vwap_data: VWAP and bands data
            delta_metrics: Delta/delta_flip data
            
        Returns:
            Signal dictionary with action, setup, confidence
        """
        signal = {
            "action": "HOLD",
            "setup_type": None,
            "confidence": 0.0,
            "notes": "",
            "entry_price": None,
            "stop_price": None,
            "target_price": None,
            "position_pct": 1.0  # Will be reduced if conservative mode
        }
        
        # Check time filters first
        if not self._is_trading_allowed(timestamp):
            signal["notes"] = "TIME_FILTER: Outside allowed session"
            return signal
        
        # Check weekend rules
        if self._is_weekend_risk_period(timestamp):
            signal["notes"] = "TIME_FILTER: Weekend risk period"
            return signal
        
        # Extract data
        close = bar_data['close']
        vwap = vwap_data.get('vwap', close)
        vwap_sd1_upper = vwap_data.get('sd1_upper', vwap * 1.01)
        vwap_sd1_lower = vwap_data.get('sd1_lower', vwap * 0.99)
        vwap_sd2_upper = vwap_data.get('sd2_upper', vwap * 1.02)
        vwap_sd2_lower = vwap_data.get('sd2_lower', vwap * 0.98)
        
        delta_direction = delta_metrics.get('direction', 'NEUTRAL')
        delta_flip = delta_metrics.get('flip', False)
        volume_ratio = bar_data.get('volume_ratio', 1.0)
        
        # Volume filter (crypto has fake volume)
        if volume_ratio < 1.2:
            signal["notes"] = "VOLUME_FILTER: Insufficient volume"
            return signal
        
        # Setup 1: VWAP Mean Reversion (Primary)
        signal = self._check_mean_reversion_setup(
            close, vwap, vwap_sd1_upper, vwap_sd1_lower,
            vwap_sd2_upper, vwap_sd2_lower,
            delta_direction, delta_flip,
            signal
        )
        
        if signal["action"] != "HOLD":
            return signal
        
        # Setup 2: Trend Continuation (Secondary, 50% size)
        signal = self._check_trend_continuation_setup(
            close, vwap, vwap_sd1_upper, vwap_sd1_lower,
            delta_direction, delta_flip,
            signal
        )
        
        return signal
    
    def _check_mean_reversion_setup(self, close, vwap, sd1_upper, sd1_lower,
                                     sd2_upper, sd2_lower,
                                     delta_direction, delta_flip,
                                     signal) -> Dict:
        """
        Check for mean reversion setup.
        Price extended beyond SD1 + delta divergence.
        """
        # Long setup: Price below SD1 + positive delta + flip
        if close < sd1_lower and delta_direction == "POSITIVE" and delta_flip:
            signal.update({
                "action": "BUY",
                "setup_type": "CRYPTO_MEAN_REVERSION_LONG",
                "confidence": 0.45,
                "entry_price": close,
                "stop_price": sd2_lower,  # 2 SDs below
                "target_price": vwap,  # Revert to VWAP
                "rr_ratio": 2.0,
                "notes": f"Price {((close/vwap)-1)*100:.1f}% below VWAP, "
                        f"delta flipping positive, volume OK"
            })
            
            if self.use_conservative_mode:
                signal["position_pct"] = 0.5
                signal["notes"] += " | Conservative mode: 50% size"
        
        # Short setup: Price above SD1 + negative delta + flip
        elif close > sd1_upper and delta_direction == "NEGATIVE" and delta_flip:
            signal.update({
                "action": "SELL",
                "setup_type": "CRYPTO_MEAN_REVERSION_SHORT",
                "confidence": 0.45,
                "entry_price": close,
                "stop_price": sd2_upper,  # 2 SDs above
                "target_price": vwap,  # Revert to VWAP
                "rr_ratio": 2.0,
                "notes": f"Price {((close/vwap)-1)*100:.1f}% above VWAP, "
                        f"delta flipping negative, volume OK"
            })
            
            if self.use_conservative_mode:
                signal["position_pct"] = 0.5
                signal["notes"] += " | Conservative mode: 50% size"
        
        return signal
    
    def _check_trend_continuation_setup(self, close, vwap, sd1_upper, sd1_lower,
                                       delta_direction, delta_flip,
                                       signal) -> Dict:
        """
        Check for trend continuation setup.
        Pullback to VWAP in established trend.
        Higher risk - use 50% position size.
        """
        # Determine trend direction from VWAP slope
        # (Would need historical data - simplified here)
        
        # Skip for now - focus on mean reversion only
        signal["notes"] = "No mean reversion setup, no trend continuation check"
        
        return signal
    
    def _is_trading_allowed(self, timestamp: datetime) -> bool:
        """Check if current time is in allowed trading session."""
        hour = timestamp.hour
        minute = timestamp.minute
        time_val = hour + minute / 60
        
        # Get day of week (0=Monday, 6=Sunday)
        weekday = timestamp.weekday()
        
        # Check weekend
        if weekday == 4 and time_val >= 16.0:  # Friday after 4pm
            return False
        if weekday == 6 and time_val < 20.0:  # Sunday before 8pm
            return False
        
        # Check allowed sessions
        sessions = self.session_config["sessions"]
        
        for session_name, session_info in sessions.items():
            if session_info["status"] == "ALLOWED":
                start = self._time_to_float(session_info["start"])
                end = self._time_to_float(session_info["end"])
                
                # Handle overnight sessions
                if session_info.get("next_day", False):
                    if time_val >= start or time_val < end:
                        return True
                else:
                    if start <= time_val < end:
                        return True
        
        return False
    
    def _is_weekend_risk_period(self, timestamp: datetime) -> bool:
        """Check if we're approaching weekend close."""
        weekday = timestamp.weekday()
        hour = timestamp.hour
        
        # Friday after 3pm - close approaching
        if weekday == 4 and hour >= 15:
            return True
        
        # Sunday before 8pm - just opened, avoid
        if weekday == 6 and hour < 20:
            return True
        
        return False
    
    def _time_to_float(self, time_str: str) -> float:
        """Convert time string to float."""
        if time_str is None:
            return 0.0
        parts = time_str.split(':')
        return int(parts[0]) + int(parts[1]) / 60


# Crypto-specific VWAP Calculator
class CryptoVWAPCalculator:
    """
    VWAP calculator for cryptocurrency.
    Uses 4-hour rolling window instead of session-based.
    """
    
    def __init__(self, rolling_window_bars: int = 16):
        # 16 bars = 4 hours of 15-minute bars
        self.rolling_window_bars = rolling_window_bars
    
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate VWAP and standard deviation bands.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            DataFrame with VWAP, SD1, SD2 columns
        """
        # Calculate typical price
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        
        # Calculate rolling VWAP (4-hour by default = 16 bars of 15-min)
        df['vwap'] = (df['typical_price'] * df['volume']).rolling(
            window=self.rolling_window_bars, min_periods=1
        ).sum() / df['volume'].rolling(window=self.rolling_window_bars, min_periods=1).sum()
        
        # Calculate standard deviation
        df['vwap_std'] = df['typical_price'].rolling(
            window=self.rolling_window_bars, min_periods=1
        ).std()
        
        # Calculate bands
        df['vwap_sd1_upper'] = df['vwap'] + df['vwap_std']
        df['vwap_sd1_lower'] = df['vwap'] - df['vwap_std']
        df['vwap_sd2_upper'] = df['vwap'] + 2 * df['vwap_std']
        df['vwap_sd2_lower'] = df['vwap'] - 2 * df['vwap_std']
        
        # Determine position relative to VWAP
        def get_vwap_position(row):
            if pd.isna(row['vwap']):
                return 'UNKNOWN'
            if row['close'] > row['vwap_sd1_upper']:
                return 'ABOVE_SD1'
            elif row['close'] < row['vwap_sd1_lower']:
                return 'BELOW_SD1'
            else:
                return 'INSIDE_SD1'
        
        df['vwap_position'] = df.apply(get_vwap_position, axis=1)
        
        return df


# Crypto Position Sizer
class CryptoPositionSizer:
    """
    Position sizing for crypto with volatility adjustments.
    """
    
    def __init__(self, 
                 base_risk_pct: float = 0.005,  # 0.5%
                 max_contracts: int = 2):
        self.base_risk_pct = base_risk_pct
        self.max_contracts = max_contracts
    
    def calculate_size(self,
                      account_equity: float,
                      entry_price: float,
                      stop_price: float,
                      atr: float,
                      volatility_regime: str = "NORMAL") -> Dict:
        """
        Calculate position size for crypto.
        
        Args:
            account_equity: Current account value
            entry_price: Planned entry price
            stop_price: Stop loss price
            atr: Current ATR
            volatility_regime: NORMAL, HIGH, or EXTREME
            
        Returns:
            Dictionary with contracts, risk amount, etc.
        """
        # Adjust risk based on volatility
        risk_multipliers = {
            "NORMAL": 1.0,   # 0.5%
            "HIGH": 0.5,     # 0.25%
            "EXTREME": 0.25  # 0.125%
        }
        
        risk_multiplier = risk_multipliers.get(volatility_regime, 1.0)
        risk_pct = self.base_risk_pct * risk_multiplier
        
        # Calculate dollar risk
        dollar_risk = account_equity * risk_pct
        
        # Calculate risk per contract
        # MBT: 0.1 BTC per contract, $0.50 tick
        stop_distance = abs(entry_price - stop_price)
        risk_per_contract = stop_distance * 0.1  # 0.1 BTC per contract
        
        if risk_per_contract <= 0:
            return {
                "contracts": 0,
                "risk_amount": 0,
                "risk_pct": 0,
                "note": "Invalid stop distance"
            }
        
        # Calculate number of contracts
        contracts = int(dollar_risk / risk_per_contract)
        contracts = min(contracts, self.max_contracts)
        contracts = max(contracts, 0)
        
        actual_risk = contracts * risk_per_contract
        
        return {
            "contracts": contracts,
            "risk_amount": actual_risk,
            "risk_pct": (actual_risk / account_equity) * 100,
            "stop_distance": stop_distance,
            "note": f"Volatility: {volatility_regime}, Risk: {risk_pct*100:.2f}%"
        }
