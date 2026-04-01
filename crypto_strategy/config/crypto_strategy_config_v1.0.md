# Crypto Futures Trading System - CME Micro Bitcoin (MBT)
# Strategy Configuration v1.0
# Created: 2026-03-22
# Status: Development Phase
# Separator: INDEPENDENT from MES Strategy

## ⚠️ IMPORTANT: SEPARATE STRATEGY ⚠️

**This is a COMPLETELY SEPARATE trading system from the MES VWAP strategy.**
- Different asset class (Crypto vs Equity Futures)
- Different risk parameters
- Different performance expectations
- Parallel operation, NOT replacement

## Strategy Overview

**Instrument**: Micro Bitcoin Futures (MBT)
**Exchange**: CME Globex (regulated, same as MES)
**Contract Specs**:
- Size: 0.1 Bitcoin per contract
- Tick Size: $0.50 per tick ($5 per Bitcoin point)
- Trading Hours: Nearly 24/5 (Sunday 18:00 - Friday 17:00 ET)
- Margin: ~$500-1,000 per contract (varies with volatility)
- Symbol: MBT

**Strategy Type**: VWAP Mean Reversion (adapted for crypto volatility)
**Timeframe**: 15-minute bars (not 5-min like MES)
**Primary Sessions**: US Hours (09:30-16:00 ET) + Evening (19:00-23:00 ET)

## Why 15-Minute Bars for Crypto?

Unlike MES (5-minute), crypto needs:
- **Smoother VWAP**: Filters out 5-minute noise
- **Wider stops**: Accommodates 10-20% daily moves
- **Fewer false signals**: Reduces whipshaw in volatile conditions
- **Manageable risk**: Prevents death by a thousand cuts

## Signal Setups

### Setup 1: Crypto VWAP Reversion (Target: 35-45% win rate)

**Long Entry Conditions**:
```
Price Action:
  - Close BELOW VWAP_SD1_LOWER (price extended below VWAP)
  
Delta Conditions:
  - Delta direction POSITIVE (buying pressure increasing)
  - Delta flip TRUE (momentum shifting up)
  
Volume:
  - Volume ratio > 1.2 (above average - crypto has fake volume)
  
Time Filter:
  - US Session: 09:30-16:00 ET (best liquidity)
  - OR Evening: 19:00-23:00 ET (acceptable)
  - NOT Asian: 00:00-09:00 ET (low liquidity, wide spreads)
  - NOT Weekend: Avoid Sunday 18:00-22:00 (gap risk)
```

**Entry Price**: Current close
**Stop Loss**: SD2_LOWER (2 standard deviations below VWAP)
**Take Profit**: VWAP (mean reversion target)
**Risk/Reward**: 1:2 minimum
**Position Size**: Max 2 contracts (conservative for crypto)
**Confidence**: 45% (lower than MES due to volatility)

**Short Entry Conditions**:
```
Price Action:
  - Close ABOVE VWAP_SD1_UPPER (price extended above VWAP)
  
Delta Conditions:
  - Delta direction NEGATIVE (selling pressure increasing)
  - Delta flip TRUE (momentum shifting down)
  
Volume:
  - Volume ratio > 1.2
  
Time Filter: Same as long
```

### Setup 2: Crypto Trend Continuation (Target: 30-40% win rate)

**Long Entry**:
```
- Price above VWAP
- VWAP sloping UP (established trend)
- Pullback to VWAP
- Delta positive on pullback
- Volume spike on bounce
```

**Short Entry**:
```
- Price below VWAP
- VWAP sloping DOWN
- Rally to VWAP
- Delta negative on rally
- Volume spike on rejection
```

**Higher risk setup - use 50% position size**

## Critical Differences from MES Strategy

### 1. Timeframe
| Parameter | MES | MBT (Crypto) | Reason |
|-----------|-----|--------------|---------|
| **Bar Size** | 5 minutes | 15 minutes | Reduce noise |
| **VWAP Reset** | Per session (RTH/Globex) | 4-hour rolling | Crypto never sleeps |
| **ATR Period** | 14 bars | 20 bars | Smoother volatility |

### 2. Risk Management
| Parameter | MES | MBT | Adjustment |
|-----------|-----|-----|------------|
| **Max Position** | 10 contracts | 2 contracts | 5x smaller |
| **Stop Width** | 1.5 × ATR | 2.5 × ATR | 67% wider |
| **Risk per Trade** | 1% of equity | 0.5% of equity | 50% lower |
| **Daily Loss Limit** | $800 | $400 | Lower threshold |
| **Consecutive Losses** | 3 stops | 2 stops | Faster circuit breaker |

### 3. Market Hours
**MES (Equity)**:
- RTH: 09:30-16:00 ET
- Globex: 18:00-17:00 ET (next day)
- Clear session boundaries

**MBT (Crypto)**:
- Continuous: Sunday 18:00 - Friday 17:00 ET
- 23 hours/day, 5 days/week
- Best liquidity: US hours 09:30-16:00 ET
- Avoid: Asian hours 00:00-09:00 ET (low liquidity)
- Critical: Close ALL positions by Friday 16:00 ET

## Time Filters for Crypto

### Session Definitions
```python
CRYPTO_SESSIONS = {
    "PRE_US": {
        "time": "18:00-09:30",
        "status": "FILTER_OUT",
        "reason": "Low liquidity, wide spreads"
    },
    "US_OPEN": {
        "time": "09:30-11:00", 
        "status": "ALLOW",
        "reason": "High volatility, good for reversion"
    },
    "US_MIDDAY": {
        "time": "11:00-14:00",
        "status": "ALLOW",
        "reason": "Moderate liquidity"
    },
    "US_CLOSE": {
        "time": "14:00-16:00",
        "status": "ALLOW",
        "reason": "High volume, good exits"
    },
    "GLOBEX_EARLY": {
        "time": "16:00-19:00",
        "status": "FILTER_OUT",
        "reason": "Low volume, false breakouts"
    },
    "GLOBEX_PRIME": {
        "time": "19:00-23:00",
        "status": "ALLOW", 
        "reason": "Evening liquidity acceptable"
    },
    "ASIAN": {
        "time": "23:00-09:30",
        "status": "FILTER_OUT",
        "reason": "Very low CME liquidity"
    }
}
```

### Weekend Risk Management
```python
WEEKEND_RULES = {
    "friday_close": "Close ALL positions by 16:00 ET Friday",
    "sunday_open": "No new positions before 20:00 ET Sunday",
    "gap_protection": "Crypto gaps 5-15% over weekends",
    "reason": "Avoid weekend gap risk on CME"
}
```

## Position Sizing for Crypto

### Formula (More Conservative than MES)
```python
def calculate_crypto_position_size(
    account_equity: float,
    entry_price: float,
    stop_price: float,
    atr: float,
    volatility_regime: str
) -> dict:
    """
    Crypto position sizing - 50% of MES risk
    """
    # Base risk: 0.5% (vs 1% for MES)
    base_risk_pct = 0.005
    
    # Volatility adjustment
    if volatility_regime == "HIGH":
        risk_pct = base_risk_pct * 0.5  # 0.25% in high vol
    elif volatility_regime == "EXTREME":
        risk_pct = base_risk_pct * 0.25  # 0.125% in extreme vol
    else:
        risk_pct = base_risk_pct  # 0.5% normal
    
    # Max position: 2 contracts (vs 10 for MES)
    max_contracts = 2
    
    # Calculate dollar risk
    dollar_risk = account_equity * risk_pct
    
    # Risk per contract
    stop_distance = abs(entry_price - stop_price)
    risk_per_contract = stop_distance * 0.1  # 0.1 BTC per contract
    
    # Number of contracts
    contracts = min(
        int(dollar_risk / risk_per_contract),
        max_contracts
    )
    
    return {
        "contracts": max(contracts, 0),
        "risk_amount": dollar_risk,
        "risk_pct": risk_pct * 100
    }
```

## Expected Performance (Realistic Targets)

### Conservative Expectations
| Metric | MES (Current) | MBT (Target) | Difference |
|--------|---------------|--------------|------------|
| **Win Rate** | 38.9% | 30-40% | Similar/Lower |
| **Profit Factor** | 2.15 | 1.5-2.0 | Slightly lower |
| **Avg Win** | $571 | $800-1,200 | Larger (wider stops) |
| **Avg Loss** | -$193 | -$400-600 | Larger |
| **Max Drawdown** | 2.0% | 8-12% | 4-6x higher |
| **Trade Frequency** | 36/60 days | 20-30/60 days | Lower |
| **Expectancy** | +0.25R | +0.15R | Lower but positive |

### Why Lower Expectations?
1. **Higher volatility** = More noise, harder to predict
2. **Less institutional flow** = VWAP less reliable
3. **News-driven** = Harder to model
4. **Weekend gaps** = Unpredictable risk

## Risk Management - Crypto Specific

### Circuit Breakers (More Aggressive than MES)

```python
CRYPTO_CIRCUIT_BREAKERS = {
    "daily_loss_limit": {
        "value": 400,  # $400 (vs $800 for MES)
        "action": "STOP_TRADING_FOR_DAY",
        "reason": "Crypto volatility"
    },
    "consecutive_losses": {
        "value": 2,  # 2 losses (vs 3 for MES)
        "action": "STOP_TRADING_FOR_DAY",
        "reason": "Momentum against us"
    },
    "max_drawdown": {
        "value": 0.10,  # 10% (vs 5% for MES)
        "action": "HALT_STRATEGY_REVIEW",
        "reason": "Crypto normal is 10-20% DD"
    },
    "weekend_exposure": {
        "value": "NO_POSITIONS_FRIDAY_16:00",
        "action": "CLOSE_ALL",
        "reason": "Weekend gap risk"
    },
    "volatility_spike": {
        "value": "ATR > 5% of price",
        "action": "REDUCE_SIZE_50%",
        "reason": "Extreme volatility"
    }
}
```

### Account Size Requirements

**Minimum for Crypto Strategy**:
- **$25,000** - Absolute minimum
- **$50,000** - Recommended (same as MES)
- **$100,000** - Ideal for proper risk management

**Why higher minimum?**
- Crypto margin requirements higher
- Need buffer for volatility
- Can't trade fractional contracts

## File System Structure

```
crypto_strategy/
├── config/
│   ├── crypto_instrument_specs.py    # MBT specs
│   ├── crypto_risk_config.py         # Risk params
│   └── crypto_session_times.py       # Trading hours
├── core/
│   ├── crypto_signal_generator.py    # Adapted signals
│   ├── crypto_vwap_calculator.py     # 4H VWAP
│   └── crypto_position_sizer.py      # Crypto sizing
├── execution/
│   └── crypto_circuit_breakers.py    # Crypto-specific
├── data/
│   └── crypto_data_loader.py         # MBT data from IBKR
├── scripts/
│   ├── backtest_crypto.py            # Crypto backtest
│   ├── download_mbt_data.py          # Get MBT from IBKR
│   └── validate_crypto.py            # Pre-live validation
└── tests/
    └── test_crypto_strategy.py       # Unit tests
```

## Implementation Checklist

### Phase 1: Setup (Week 1)
- [ ] Create crypto directory structure
- [ ] Write crypto_instrument_specs.py
- [ ] Adapt signal generator for 15-min bars
- [ ] Modify position sizer for crypto risk
- [ ] Implement weekend close logic

### Phase 2: Data (Week 2)
- [ ] Download 3 months MBT historical from IBKR
- [ ] Calculate 4-hour rolling VWAP
- [ ] Compute crypto-specific indicators
- [ ] Validate data quality

### Phase 3: Backtest (Week 3)
- [ ] Run initial backtest
- [ ] Analyze results vs MES baseline
- [ ] Optimize parameters if needed
- [ ] Document performance metrics

### Phase 4: Paper Trading (Week 4-6)
- [ ] Deploy in paper mode
- [ ] Collect 20-30 trades minimum
- [ ] Compare to MES performance
- [ ] Validate risk management

### Phase 5: Decision (Week 7)
- [ ] If PF > 1.5: Proceed to live
- [ ] If 1.0 < PF < 1.5: Optimize further
- [ ] If PF < 1.0: Abandon or redesign

## Success Criteria

**Minimum Viable**:
- Profit Factor > 1.5
- Win rate > 30%
- Positive expectancy (+0.1R per trade)
- Max drawdown < 15%
- 20+ trades for statistical significance

**Good Performance**:
- Profit Factor > 1.8
- Win rate > 35%
- Expectancy +0.2R per trade
- Max drawdown < 10%
- Consistent weekly performance

**Excellent Performance**:
- Profit Factor > 2.0
- Win rate > 40%
- Expectancy +0.3R per trade
- Max drawdown < 8%
- Beats buy-and-hold Bitcoin

## Diversification Benefits

### Why Trade Both MES + MBT?

**Correlation**:
- MES ↔ MBT correlation: ~0.3-0.5 (moderate)
- When stocks crash, crypto often pumps (and vice versa)
- Different market drivers

**Risk Distribution**:
- MES: Regulated, institutional, lower volatility
- MBT: Emerging, retail-heavy, higher volatility
- Combined portfolio smoother equity curve

**Opportunity Set**:
- MES: Best during US equity sessions
- MBT: Can trade evening hours (19:00-23:00 ET)
- More setups per day across both

## Documentation Files

1. **crypto_strategy_config_v1.0.md** (this file) - Complete strategy specification
2. **crypto_instrument_specs.py** - MBT contract specifications
3. **crypto_vs_mes_comparison.md** - Side-by-side comparison
4. **crypto_backtest_results.md** - Results after testing
5. **crypto_live_trading_log.md** - Paper/live trade journal

## ⚠️ Final Warning ⚠️

**Crypto is NOT equities:**
- 10x more volatile
- Different market structure
- News/event sensitive
- Weekend gap risk
- Regulatory uncertainty

**This is a HIGH-RISK strategy.**
- Only allocate 10-20% of capital to crypto
- Trade at 50% size of MES
- Use wider stops
- Accept larger drawdowns
- **NEVER risk money you can't afford to lose**

## Next Steps

1. **Review this config** - Confirm parameters
2. **Set up file structure** - Create crypto_strategy/ directory
3. **Download MBT data** - Get 3 months from IBKR
4. **Run backtest** - Validate on historical data
5. **Paper trade** - Test with virtual money first
6. **Go live** - Only if profitable on paper

---

**Status**: Ready for development
**Parallel to MES**: Yes - runs independently
**Risk Level**: Higher than MES
**Expected Return**: Higher volatility, similar PF

**DO NOT proceed until you understand:**
✅ Crypto is 10x more volatile than equities
✅ You can lose 50% in a single week
✅ This is experimental, not proven
✅ MES strategy continues separately

**Created**: 2026-03-22
**Last Updated**: 2026-03-22
**Version**: 1.0
**Status**: Development Phase
