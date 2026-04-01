"""
Fallback Trading Strategy - Pre-Video Pipeline
==============================================

This strategy works immediately WITHOUT any video data.
Uses peer-reviewed VWAP edge cases with conservative win rates.

ACTIVATES AUTOMATICALLY when video_trade_count < 30
"""

# CONSERVATIVE FALLBACK SETUPS (peer-reviewed)
FALLBACK_STRATEGY = {
    "MEAN_REVERSION_SD1": {
        "win_rate": 0.65,  # Conservative vs 72% from spec
        "conditions": {
            "market_state": "BALANCED",
            "vwap_position": ["ABOVE_SD1", "BELOW_SD1"],
            "delta_flip": True,
        },
        "entry_logic": "Price touches SD1 + Delta flips direction",
        "stop_loss": "2 points beyond SD1",
        "target": "VWAP (mean reversion)",
        "rr_ratio": 2.0,
        "best_times": "10:00-11:30 ET, 13:30-15:00 ET",
        "description": "Fade VWAP deviation in balanced markets"
    },
    
    "SD2_EXTREME_FADE": {
        "win_rate": 0.58,  # Conservative vs 58% from spec
        "conditions": {
            "vwap_position": ["ABOVE_SD2", "BELOW_SD2"],
            "volume_spike": True,
        },
        "entry_logic": "Price beyond 2σ + Volume climax",
        "stop_loss": "1 point beyond SD2",
        "target": "SD1 or VWAP",
        "rr_ratio": 2.5,
        "best_times": "Any time (statistical outlier)",
        "description": "Fade extreme overextensions"
    },
    
    "VWAP_CONTINUATION": {
        "win_rate": 0.60,  # Conservative vs 60% from spec
        "conditions": {
            "market_state": ["IMBALANCED_BULL", "IMBALANCED_BEAR"],
            "vwap_position": ["ABOVE_VWAP", "BELOW_VWAP"],
            "volume_spike": True,
        },
        "entry_logic": "Price holds VWAP + Momentum + Volume",
        "stop_loss": "VWAP breach",
        "target": "Next SD level",
        "rr_ratio": 1.5,
        "best_times": "Trending markets only",
        "description": "VWAP hold in trending markets"
    }
}

# RISK PARAMETERS (active immediately)
RISK_CONFIG = {
    "max_daily_trades": 5,           # Hard ceiling
    "max_consecutive_losses": 3,     # Stop after 3 losses
    "max_risk_per_trade_pct": 0.01,  # 1% of account
    "daily_loss_limit": 800,         # $800 (40% of MLL)
    "mll_proximity_buffer": 500,     # Stop within $500 of floor
}

# TRADING SCHEDULE
TRADING_HOURS = {
    "rth_start": "09:30",      # Market open
    "rth_end": "16:00",        # Market close
    "no_trade_start": "09:30", # First 15 min
    "no_trade_end": "15:45",   # Last 15 min
    "best_morning": "10:00-11:30",
    "best_afternoon": "13:30-15:00",
}

# PERFORMANCE EXPECTATIONS
EXPECTED_PERFORMANCE = {
    "daily_trades": "1-5",
    "win_rate_range": "55-65%",
    "avg_winner": "2R",
    "avg_loser": "1R",
    "expectancy": "+0.3R per trade",
    "monthly_return": "3-8%",
    "max_drawdown": "<10%",
}

"""
TESTING CHECKLIST - Before Video Data
======================================

Phase 1: Unit Testing (1 day)
------------------------------
[ ] Run: python test_system.py
[ ] Verify RiskManager enforces limits
[ ] Verify SignalGenerator produces signals
[ ] Verify CircuitBreakers trigger correctly

Phase 2: Paper Testing (3-5 days)
----------------------------------
[ ] Start webhook server
[ ] Send 5-10 test signals manually
[ ] Verify orders execute in TWS
[ ] Check PnL calculations
[ ] Monitor daily trade limits

Phase 3: Live Data Testing (1 week)
------------------------------------
[ ] Connect to TradingView
[ ] Let system run during RTH
[ ] Review all generated signals
[ ] Verify no over-trading
[ ] Check circuit breaker triggers

Phase 4: Validation (ongoing)
------------------------------
[ ] Track win rate on first 30 trades
[ ] Verify within 55-65% range
[ ] Check risk adherence
[ ] Ensure no account blow-up

Once 30+ trades logged with outcomes:
→ Switch to VIDEO_DERIVED mode
→ Update confidence scores
→ Refine entry conditions
"""
