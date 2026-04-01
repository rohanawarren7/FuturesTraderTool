"""
Crypto Instrument Specifications
================================

Contract specs for CME Micro Bitcoin Futures (MBT)
Used by crypto strategy - completely separate from MES specs.
"""

CRYPTO_INSTRUMENT_SPECS = {
    "MBT": {
        # Contract Specifications
        "symbol": "MBT",
        "name": "Micro Bitcoin Futures",
        "exchange": "CME",
        "currency": "USD",
        
        # Contract Size & Value
        "bitcoin_per_contract": 0.1,  # 0.1 BTC per contract
        "tick_size": 0.50,  # $0.50 per tick
        "tick_value": 0.50,  # $0.50 per tick (0.1 BTC × $5 move = $0.50)
        "point_value": 5.0,  # $5 per $1 move in Bitcoin
        
        # Margin Requirements (approximate, check IBKR for current)
        "margin_initial": 1000,  # ~$1,000 initial margin per contract
        "margin_maintenance": 800,  # ~$800 maintenance
        "margin_overnight": 1200,  # Higher overnight
        
        # Trading Hours (ET)
        "trading_hours": {
            "sunday_open": "18:00",  # Sunday evening
            "friday_close": "17:00",  # Friday evening
            "daily_break_start": None,  # No daily break (continuous)
            "daily_break_end": None,
        },
        "timezone": "America/New_York",
        
        # Expiration
        "contract_months": ["H", "M", "U", "Z"],  # Mar, Jun, Sep, Dec
        "rollover_days_before_expiry": 3,
        
        # Liquidity & Slippage Estimates
        "avg_daily_volume": 50000,  # ~50K contracts/day (check current)
        "typical_spread_ticks": 2,  # $1.00 typical spread
        "slippage_estimate": 0.02,  # 2% slippage in fast markets
        
        # Strategy-Specific Parameters
        "max_position_contracts": 2,  # Conservative limit
        "max_position_value_btc": 0.2,  # Max 0.2 BTC exposure
        "recommended_position": 1,  # Start with 1 contract
        
        # Bar Settings
        "bar_interval": "15min",  # 15-minute bars (not 5-min like MES)
        "vwap_rolling_period": "4H",  # 4-hour rolling VWAP
        "atr_period": 20,  # 20 bars for ATR (more than MES)
        
        # Session Filters
        "allowed_sessions": [
            "US_OPEN",      # 09:30-11:00 ET
            "US_MIDDAY",    # 11:00-14:00 ET
            "US_CLOSE",     # 14:00-16:00 ET
            "GLOBEX_PRIME", # 19:00-23:00 ET
        ],
        "filtered_sessions": [
            "PRE_US",       # 18:00-09:30 ET
            "GLOBEX_EARLY", # 16:00-19:00 ET
            "ASIAN",        # 23:00-09:30 ET
        ],
        
        # Risk Multipliers (relative to MES)
        "volatility_multiplier": 3.0,  # 3x more volatile
        "stop_multiplier": 2.5,  # 2.5x wider stops
        "position_size_divisor": 5,  # 1/5th the size
        "risk_per_trade_divisor": 2,  # 50% of MES risk
    }
}


# Risk Configuration for Crypto Strategy
CRYPTO_RISK_CONFIG = {
    # Position Sizing
    "base_risk_per_trade_pct": 0.005,  # 0.5% (vs 1% for MES)
    "max_risk_high_vol_pct": 0.0025,  # 0.25% in high vol
    "max_risk_extreme_vol_pct": 0.00125,  # 0.125% in extreme vol
    
    # Daily Limits
    "max_daily_trades": 3,  # Lower than MES (5)
    "max_consecutive_losses": 2,  # Stop after 2 (vs 3 for MES)
    "daily_loss_limit_dollars": 400,  # $400 (vs $800 for MES)
    "daily_loss_limit_pct": 0.008,  # 0.8% of account
    
    # Drawdown Limits
    "max_drawdown_pct": 0.10,  # 10% max DD (vs 5% for MES)
    "warning_drawdown_pct": 0.06,  # 6% warning level
    "halt_drawdown_pct": 0.10,  # Halt at 10%
    
    # Circuit Breakers
    "circuit_breakers": {
        "volatility_spike": {
            "condition": "atr > 0.05 * price",  # ATR > 5% of price
            "action": "reduce_size_50pct",
            "reason": "Extreme volatility"
        },
        "weekend_approach": {
            "condition": "friday_after_15:00_et",
            "action": "close_all_positions",
            "reason": "Weekend gap risk"
        },
        "sunday_open": {
            "condition": "sunday_before_20:00_et",
            "action": "no_new_positions",
            "reason": "Avoid Sunday volatility"
        },
        "low_liquidity": {
            "condition": "asian_session",
            "action": "no_trading",
            "reason": "Wide spreads, low volume"
        }
    },
    
    # Account Protection
    "min_account_balance": 25000,  # $25K minimum
    "recommended_account": 50000,  # $50K recommended
    "max_position_contracts": 2,  # Absolute max
    "max_margin_utilization_pct": 0.20,  # 20% of account
}


# Session Configuration
CRYPTO_SESSION_CONFIG = {
    "timezone": "America/New_York",
    "sessions": {
        "PRE_US": {
            "start": "18:00",
            "end": "09:30",
            "next_day": True,  # Runs overnight into next day
            "status": "BLOCKED",
            "liquidity": "LOW",
            "spreads": "WIDE",
            "notes": "Low liquidity, avoid trading"
        },
        "US_OPEN": {
            "start": "09:30",
            "end": "11:00",
            "status": "ALLOWED",
            "liquidity": "HIGH",
            "spreads": "TIGHT",
            "notes": "Best session for entries"
        },
        "US_MIDDAY": {
            "start": "11:00",
            "end": "14:00",
            "status": "ALLOWED",
            "liquidity": "MEDIUM",
            "spreads": "NORMAL",
            "notes": "Moderate activity, OK to trade"
        },
        "US_CLOSE": {
            "start": "14:00",
            "end": "16:00",
            "status": "ALLOWED",
            "liquidity": "HIGH",
            "spreads": "TIGHT",
            "notes": "Good for exits, high volume"
        },
        "GLOBEX_EARLY": {
            "start": "16:00",
            "end": "19:00",
            "status": "BLOCKED",
            "liquidity": "LOW",
            "spreads": "WIDE",
            "notes": "Post-CME close, low activity"
        },
        "GLOBEX_PRIME": {
            "start": "19:00",
            "end": "23:00",
            "status": "ALLOWED",
            "liquidity": "MEDIUM",
            "spreads": "NORMAL",
            "notes": "Evening session, acceptable"
        },
        "ASIAN": {
            "start": "23:00",
            "end": "09:30",
            "next_day": True,
            "status": "BLOCKED",
            "liquidity": "VERY_LOW",
            "spreads": "VERY_WIDE",
            "notes": "Avoid - lowest CME liquidity"
        }
    },
    "weekend_rules": {
        "friday_close_time": "16:00",
        "sunday_open_delay": "20:00",
        "enforce_flat_weekend": True,
        "reason": "Crypto gaps 5-15% over weekends"
    }
}


# Performance Targets
CRYPTO_PERFORMANCE_TARGETS = {
    "minimum_viable": {
        "profit_factor": 1.5,
        "win_rate": 0.30,
        "expectancy_r": 0.10,
        "max_drawdown_pct": 0.15,
        "min_trades": 20
    },
    "good_performance": {
        "profit_factor": 1.8,
        "win_rate": 0.35,
        "expectancy_r": 0.20,
        "max_drawdown_pct": 0.10
    },
    "excellent_performance": {
        "profit_factor": 2.0,
        "win_rate": 0.40,
        "expectancy_r": 0.30,
        "max_drawdown_pct": 0.08
    }
}


# Comparison to MES
CRYPTO_VS_MES = {
    "timeframe": {
        "mes": "5min bars",
        "mbt": "15min bars",
        "reason": "Reduce crypto noise"
    },
    "vwap": {
        "mes": "Session-based (RTH/Globex)",
        "mbt": "4-hour rolling",
        "reason": "Crypto never sleeps"
    },
    "max_position": {
        "mes": 10,
        "mbt": 2,
        "reason": "5x smaller for volatility"
    },
    "risk_per_trade": {
        "mes": "1.0%",
        "mbt": "0.5%",
        "reason": "50% of MES risk"
    },
    "stop_width": {
        "mes": "1.5x ATR",
        "mbt": "2.5x ATR",
        "reason": "67% wider for volatility"
    },
    "daily_loss_limit": {
        "mes": "$800",
        "mbt": "$400",
        "reason": "Lower threshold"
    },
    "max_drawdown": {
        "mes": "5%",
        "mbt": "10%",
        "reason": "Crypto normal is higher"
    },
    "expected_pf": {
        "mes": "2.0-2.5",
        "mbt": "1.5-2.0",
        "reason": "Harder to predict"
    }
}
