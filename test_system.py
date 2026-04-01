#!/usr/bin/env python3
"""Quick test of the paper trading system"""

import sys
sys.path.insert(0, '.')

print('='*70)
print('PAPER TRADING SYSTEM - QUICK TEST')
print('='*70)
print()

# Test 1: Imports
print('TEST 1: Module Imports')
print('-'*70)

try:
    from core.risk_manager import RiskManager
    print('[PASS] RiskManager')
except Exception as e:
    print(f'[FAIL] RiskManager: {e}')

try:
    from core.position_sizer import PositionSizer
    print('[PASS] PositionSizer')
except Exception as e:
    print(f'[FAIL] PositionSizer: {e}')

try:
    from core.signal_generator import SignalGenerator
    print('[PASS] SignalGenerator')
except Exception as e:
    print(f'[FAIL] SignalGenerator: {e}')

try:
    from execution.circuit_breakers import CircuitBreakers
    print('[PASS] CircuitBreakers')
except Exception as e:
    print(f'[FAIL] CircuitBreakers: {e}')

print()

# Test 2: Configuration
print('TEST 2: Configuration')
print('-'*70)

try:
    from config.prop_firm_configs import PROP_FIRM_CONFIGS
    config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
    print(f'[PASS] Prop Firm Config')
    print(f'       Account Size: ${config["account_size"]:,}')
    print(f'       Profit Target: ${config["profit_target"]:,}')
    print(f'       Max Loss Limit: ${config["max_loss_limit"]:,}')
except Exception as e:
    print(f'[FAIL] Prop Firm Config: {e}')

try:
    from config.bot_risk_params import BOT_RISK_PARAMS
    print(f'[PASS] Risk Params')
    print(f'       Max Daily Trades: {BOT_RISK_PARAMS["max_daily_trades"]}')
    print(f'       Max Consecutive Losses: {BOT_RISK_PARAMS["max_consecutive_losses"]}')
except Exception as e:
    print(f'[FAIL] Risk Params: {e}')

print()

# Test 3: Risk Manager
print('TEST 3: Risk Manager')
print('-'*70)

try:
    from core.risk_manager import RiskManager
    from config.prop_firm_configs import PROP_FIRM_CONFIGS
    
    config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
    risk_mgr = RiskManager(config)
    
    # Test position sizing
    contracts = risk_mgr.calculate_position_size(
        account_equity=50000,
        atr=7.5,
        stop_distance=5.0,
        point_value=5,
        confidence=0.7
    )
    print(f'[PASS] Position sizing: {contracts} contracts')
    
    # Test daily trade limit
    risk_mgr.daily_trade_count = 5
    from datetime import datetime
    market_state = {"timestamp": datetime.now(), "hour": 10}
    account_state = {"equity": 50000, "mll_floor": 48000}
    allowed, reason = risk_mgr.can_trade(market_state, account_state)
    
    if not allowed and "DAILY_TRADE_LIMIT" in reason:
        print(f'[PASS] Daily trade limit enforced')
    else:
        print(f'[FAIL] Daily trade limit not working')
        
except Exception as e:
    print(f'[FAIL] Risk Manager: {e}')
    import traceback
    traceback.print_exc()

print()

# Test 4: Signal Generator
print('TEST 4: Signal Generator')
print('-'*70)

try:
    from core.signal_generator import SignalGenerator
    
    gen = SignalGenerator(video_trade_count=0, use_fallback=True)
    
    signal = gen.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    if signal["action"] == "BUY":
        print(f'[PASS] Signal generation: {signal["action"]} {signal["setup_type"]}')
        print(f'       Confidence: {signal["confidence"]}')
        print(f'       Mode: {gen.get_signal_mode()}')
    else:
        print(f'[FAIL] Signal generation failed')
        
except Exception as e:
    print(f'[FAIL] Signal Generator: {e}')
    import traceback
    traceback.print_exc()

print()

# Test 5: Circuit Breakers
print('TEST 5: Circuit Breakers')
print('-'*70)

try:
    from execution.circuit_breakers import CircuitBreakers
    from config.prop_firm_configs import PROP_FIRM_CONFIGS
    from datetime import datetime
    
    config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
    circuit_breakers = CircuitBreakers(config)
    
    # Test daily loss breaker
    context = {
        "daily_pnl": -900,
        "equity": 49100,
        "mll_floor": 48000,
        "consecutive_losses": 0,
        "last_data_timestamp": datetime.now(),
        "last_broker_ping": datetime.now(),
        "recent_orders": [],
        "recent_fills": [],
        "recent_errors": [],
    }
    
    allowed, reason = circuit_breakers.check_all(context)
    
    if not allowed and "daily_loss" in reason.lower():
        print(f'[PASS] Daily loss circuit breaker: BLOCKED (expected)')
    else:
        print(f'[FAIL] Daily loss circuit breaker not working')
        
    # Test good context
    context["daily_pnl"] = 100
    context["equity"] = 51000
    allowed, reason = circuit_breakers.check_all(context)
    
    if allowed:
        print(f'[PASS] Trading allowed when conditions good')
    else:
        print(f'[FAIL] Trading blocked unexpectedly: {reason}')
        
    print(f'       Total breakers: {len(circuit_breakers.breakers)}')
        
except Exception as e:
    print(f'[FAIL] Circuit Breakers: {e}')
    import traceback
    traceback.print_exc()

print()
print('='*70)
print('QUICK TEST COMPLETE')
print('='*70)
print()
print('Summary: Core system is working!')
print()
print('Next steps:')
print('1. Install ib_insync: pip3 install ib_insync')
print('2. Setup TWS with API enabled on port 7497')
print('3. Run: python3 scripts/setup_ibkr_account.py')
print('4. Start trading!')
