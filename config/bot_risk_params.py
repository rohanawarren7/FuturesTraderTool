BOT_RISK_PARAMS = {
    # Daily circuit breakers
    "daily_stop_loss_pct_of_mll": 0.40,   # Stop trading if daily loss hits 40% of MLL
    "daily_profit_target_usd": 600,        # Optional: stop trading after $600 profit day
    "profit_protect_threshold_usd": 400,   # Reduce size by 50% once $400 up on the day

    # Position sizing and trade limits
    "max_daily_trades": 5,                # Hard ceiling on daily trade frequency
    "max_concurrent_positions": 1,        # No pyramiding in early phase
    "max_consecutive_losses": 3,          # Stop trading after 3 consecutive losses
    "default_contracts": 1,               # Start with 1 contract until 50 live trades proven
    "scale_up_threshold_trades": 50,      # Increase to 2 contracts after 50 profitable trades
    "max_contracts_in_use": 3,            # Never exceed 3 contracts in early phase
    
    # Risk per trade
    "max_risk_per_trade_pct": 0.01,       # 1% of account equity per trade
    "position_size_formula": "kelly_half", # Half-Kelly criterion
    "volatility_lookback": 20,            # ATR period for volatility adjustment
    
    # Breathing room for stops
    "breathing_room_multiplier": 1.5,     # Space between stop and MLL floor
    
    # Correlation management
    "correlation_threshold": 0.7,         # Block correlated setups
    "min_time_between_trades_minutes": 5, # Minimum time between trades

    # Trade timing filters
    "no_trade_open_minutes": 15,          # No trades in first 15 min after RTH open (9:30 ET)
    "no_trade_close_minutes": 15,         # No trades in last 15 min before RTH close (4:00 ET)
    "no_trade_before_london_minutes": 5,  # No trades 5 min before London open (3:00 AM ET)
    "rth_start_et": "09:30",
    "rth_end_et": "16:00",
    "london_open_et": "03:00",
    "preferred_window_start": "09:45",    # Optimal VWAP window start
    "preferred_window_end": "14:00",      # Optimal VWAP window end

    # News blackout (Apex mandatory, Topstep recommended)
    "news_blackout_minutes_before": 2,
    "news_blackout_minutes_after": 2,
    "high_impact_events": [
        "NFP", "CPI", "FOMC", "FOMC_MINUTES", "GDP", "PCE", "PPI", "RETAIL_SALES"
    ],

    # Order execution
    "entry_order_type": "LIMIT",
    "exit_order_type": "LIMIT",
    "stop_order_type": "STOP_MARKET",
    "max_slippage_ticks": 2,

    # Drawdown monitoring
    "drawdown_monitor_interval_seconds": 5,
    "emergency_flatten_on_breach": True,
    
    # MLL proximity
    "mll_proximity_pct": 0.10,            # Within 10% of MLL floor = halt trading
}

# Separate risk params for the combine phase — more conservative
COMBINE_RISK_PARAMS = {
    **BOT_RISK_PARAMS,
    "daily_stop_loss_pct_of_mll": 0.30,  # Extra conservative during combine
    "daily_profit_target_usd": 300,
    "profit_protect_threshold_usd": 200,
    "default_contracts": 1,
    "max_contracts_in_use": 2,
}
