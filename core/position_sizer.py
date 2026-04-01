"""
Position Sizing Module for VWAP Trading Bot.
Calculates optimal position size based on volatility, account size, and signal confidence.
"""

from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd

from core.risk_manager import RiskManager


class PositionSizer:
    """
    Calculates position size using volatility-adjusted Kelly criterion.
    Integrates with RiskManager for comprehensive risk control.
    """
    
    def __init__(self, risk_manager: RiskManager, instrument_specs: dict):
        """
        Initialize position sizer.
        
        Args:
            risk_manager: RiskManager instance for risk constraints
            instrument_specs: Specifications from INSTRUMENT_SPECS
        """
        self.risk_manager = risk_manager
        self.instrument = instrument_specs
        self.point_value = instrument_specs.get("point_value", 5)
        self.tick_size = instrument_specs.get("tick_size", 0.25)
        
    def calculate_size(self,
                      account_equity: float,
                      entry_price: float,
                      stop_price: float,
                      atr: float,
                      signal_confidence: float,
                      market_state: str = "BALANCED") -> dict:
        """
        Calculate optimal position size for a trade.
        
        Args:
            account_equity: Current account equity
            entry_price: Proposed entry price
            stop_price: Stop loss price
            atr: 14-period Average True Range
            signal_confidence: Signal confidence (0-1)
            market_state: Current market regime
            
        Returns:
            Dictionary with contracts, risk_amount, and sizing details
        """
        # Calculate stop distance
        stop_distance = abs(entry_price - stop_price)
        stop_ticks = stop_distance / self.tick_size
        
        # Base risk amount (1% of equity)
        base_risk_pct = self.risk_manager.bot_params["max_risk_per_trade_pct"]
        max_risk_dollars = account_equity * base_risk_pct
        
        # Volatility adjustment
        vol_adjustment = self._calculate_volatility_adjustment(atr, market_state)
        
        # Confidence adjustment (Kelly-inspired)
        confidence_adjustment = self._calculate_confidence_adjustment(signal_confidence)
        
        # Market state adjustment
        state_adjustment = self._calculate_state_adjustment(market_state)
        
        # Combined adjustment
        total_adjustment = vol_adjustment * confidence_adjustment * state_adjustment
        
        # Adjusted risk amount
        adjusted_risk = max_risk_dollars * total_adjustment
        
        # Calculate contracts
        if stop_distance <= 0:
            contracts = 1
        else:
            risk_per_contract = stop_distance * self.point_value
            contracts = int(adjusted_risk / risk_per_contract)
        
        # Apply hard limits
        max_contracts = self.risk_manager.prop_config.get("max_contracts", 5)
        contracts = max(1, min(contracts, max_contracts))
        
        # Calculate actual risk
        actual_risk = contracts * stop_distance * self.point_value
        
        # Calculate position value
        position_value = contracts * entry_price * self.point_value
        
        return {
            "contracts": contracts,
            "risk_dollars": actual_risk,
            "risk_pct": (actual_risk / account_equity) * 100,
            "position_value": position_value,
            "stop_distance": stop_distance,
            "stop_ticks": stop_ticks,
            "volatility_adjustment": vol_adjustment,
            "confidence_adjustment": confidence_adjustment,
            "state_adjustment": state_adjustment,
            "sizing_method": self.risk_manager.bot_params["position_size_formula"],
        }
    
    def _calculate_volatility_adjustment(self, atr: float, market_state: str) -> float:
        """
        Adjust position size based on current volatility.
        
        Higher volatility = smaller position size.
        """
        if atr <= 0:
            return 1.0
        
        # Typical ATR for MES is around 5-10 points
        # Normalize around 7.5 points
        typical_atr = 7.5
        vol_ratio = atr / typical_atr
        
        # Inverse relationship: high vol = reduce size
        # Use square root to dampen the effect
        adjustment = 1 / np.sqrt(max(0.5, vol_ratio))
        
        # Cap adjustment between 0.5x and 1.5x
        return max(0.5, min(1.5, adjustment))
    
    def _calculate_confidence_adjustment(self, confidence: float) -> float:
        """
        Adjust position size based on signal confidence.
        
        Uses half-Kelly criterion:
        - High confidence = larger position
        - Low confidence = smaller position
        """
        if confidence <= 0:
            return 0.5  # Minimum size
        
        # Half-Kelly: scale position with confidence
        # At 0.5 confidence: 0.5x size
        # At 0.7 confidence: 0.7x size  
        # At 0.9 confidence: 0.9x size
        adjustment = confidence
        
        # Ensure minimum 0.5x, maximum 1.0x
        return max(0.5, min(1.0, adjustment))
    
    def _calculate_state_adjustment(self, market_state: str) -> float:
        """
        Adjust position size based on market regime.
        
        More conservative in volatile/uncertain states.
        """
        adjustments = {
            "BALANCED": 1.0,          # Normal sizing
            "IMBALANCED_BULL": 1.0,   # Normal sizing
            "IMBALANCED_BEAR": 1.0,   # Normal sizing
            "VOLATILE_TRANS": 0.7,    # Reduce size in volatile transitions
            "LOW_ACTIVITY": 0.5,      # Significantly reduce in low activity
        }
        
        return adjustments.get(market_state, 0.8)
    
    def validate_position(self, 
                         proposed_size: int,
                         account_equity: float,
                         current_positions: list) -> tuple[bool, str]:
        """
        Validate if proposed position size is acceptable.
        
        Args:
            proposed_size: Number of contracts requested
            account_equity: Current account equity
            current_positions: List of current open positions
            
        Returns:
            (valid, reason) - reason is empty if valid
        """
        # Check max concurrent positions
        max_positions = self.risk_manager.bot_params["max_concurrent_positions"]
        if len(current_positions) >= max_positions:
            return False, f"Max concurrent positions ({max_positions}) reached"
        
        # Check max contracts per prop firm rules
        max_contracts = self.risk_manager.prop_config.get("max_contracts", 5)
        if proposed_size > max_contracts:
            return False, f"Exceeds max contracts ({max_contracts})"
        
        # Check position concentration
        if current_positions:
            total_exposure = sum(p.get("contracts", 0) for p in current_positions)
            total_exposure += proposed_size
            
            # Don't exceed 50% of max contracts in total exposure
            max_total = max_contracts * 0.5
            if total_exposure > max_total:
                return False, f"Total exposure ({total_exposure}) would exceed {max_total}"
        
        return True, ""
    
    def adjust_for_correlation(self,
                              base_size: int,
                              correlated_exposure: float) -> int:
        """
        Reduce position size if already exposed to correlated instruments.
        
        Args:
            base_size: Calculated position size
            correlated_exposure: Current exposure to correlated instruments (0-1)
            
        Returns:
            Adjusted position size
        """
        # Reduce size based on existing correlated exposure
        # If already 50% exposed to correlated instruments, reduce new position by 50%
        correlation_threshold = self.risk_manager.bot_params["correlation_threshold"]
        
        if correlated_exposure >= correlation_threshold:
            reduction = correlated_exposure / correlation_threshold
            adjusted_size = int(base_size * (1 - reduction * 0.5))
            return max(1, adjusted_size)
        
        return base_size
    
    def get_breathing_room_stop(self,
                               entry_price: float,
                               stop_price: float,
                               vwap_std: float) -> float:
        """
        Calculate stop loss with breathing room multiplier.
        
        Ensures stop isn't too tight relative to normal volatility.
        
        Args:
            entry_price: Entry price
            stop_price: Initial stop price
            vwap_std: VWAP standard deviation
            
        Returns:
            Adjusted stop price with breathing room
        """
        multiplier = self.risk_manager.bot_params["breathing_room_multiplier"]
        
        # Calculate minimum stop distance based on VWAP std
        min_distance = vwap_std * multiplier
        
        current_distance = abs(entry_price - stop_price)
        
        if current_distance < min_distance:
            # Adjust stop to have proper breathing room
            if entry_price > stop_price:  # Long position
                return entry_price - min_distance
            else:  # Short position
                return entry_price + min_distance
        
        return stop_price
    
    def calculate_kelly_fraction(self,
                                 win_rate: float,
                                 avg_win: float,
                                 avg_loss: float) -> float:
        """
        Calculate Kelly criterion fraction for position sizing.
        
        Kelly Formula: f = (p*b - q) / b
        where p = win probability, q = loss probability (1-p)
        b = avg_win / avg_loss (win/loss ratio)
        
        We use Half-Kelly for safety: f/2
        
        Args:
            win_rate: Historical win rate (0-1)
            avg_win: Average winning trade in dollars
            avg_loss: Average losing trade in dollars (positive number)
            
        Returns:
            Kelly fraction (0-1) - use Half-Kelly for actual sizing
        """
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        
        # Calculate win/loss ratio
        b = avg_win / avg_loss
        
        # Kelly fraction
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b
        
        # Half-Kelly for safety
        half_kelly = kelly / 2
        
        # Ensure reasonable bounds
        return max(0.0, min(0.25, half_kelly))  # Cap at 25% of Kelly
