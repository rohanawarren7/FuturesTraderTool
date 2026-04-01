# Testing Strategy on Non-CME Futures & Bitcoin

## Overview
Testing the VWAP mean reversion strategy on alternative instruments to validate edge persistence across different markets.

## Available Options via IBKR API

### 1. Non-CME Futures (Alternative Exchanges)

#### Eurex (European Exchange)
**Available Instruments**:
- **FDAX** - DAX Futures (Germany)
- **FESX** - EURO STOXX 50 Futures
- **FGBL** - Euro-Bund Futures

**Pros**:
- Different market hours (European timezone)
- Different volatility characteristics
- Diversification from US markets

**Cons**:
- Different contract specs (EUR margin, different tick sizes)
- Less liquid than CME for some contracts
- Currency risk (EUR/USD fluctuations)

**Data Cost**: $15-25/month for Eurex data

#### ICE Futures (Intercontinental Exchange)
**Available Instruments**:
- **B** - Brent Crude Oil
- **G** - Low Sulfur Gasoil
- **T** - WTI Crude Oil

**Pros**:
- Commodity exposure (different asset class)
- High volatility (good for mean reversion)
- Different market drivers

**Cons**:
- Very different from equity index futures
- Seasonal patterns
- News/event sensitive

**Data Cost**: $20-30/month

#### NYMEX/COMEX (Part of CME Group)
**Available Instruments**:
- **CL** - Crude Oil
- **GC** - Gold
- **NG** - Natural Gas
- **SI** - Silver

**Status**: Still CME Group but different asset classes

---

### 2. Bitcoin Futures (CME)

#### CME Bitcoin Futures (BTC)
**Contract Specs**:
- Symbol: **BTC**
- Size: 5 Bitcoin per contract
- Tick Size: $25 per contract ($5 per Bitcoin)
- Trading Hours: Nearly 24/7 (CME Globex)
- Margin: ~$25,000+ per contract (very high)

**Micro Bitcoin Futures (MBT)**
- Symbol: **MBT**
- Size: 0.1 Bitcoin per contract
- Tick Size: $0.50 per contract ($5 per Bitcoin equivalent)
- Trading Hours: Nearly 24/7
- Margin: ~$500-1,000 per contract (more accessible)

#### Why Bitcoin Could Work

**Pros**:
1. **High Volatility**: Creates more mean reversion opportunities
2. **VWAP Works Well**: Institutional algos trade against VWAP
3. **24/7 Market**: More trading sessions to test
4. **Different Regime**: Crypto vs equity behavior
5. **IBKR Access**: Available via CME Globex

**Cons**:
1. **Extreme Volatility**: 10-20% daily moves (vs 1-2% for ES)
2. **Different Hours**: 23/5 trading (almost always open)
3. **Higher Margins**: Need larger account
4. **Gap Risk**: Weekend gaps are huge
5. **Liquidity**: Less liquid than ES, especially overnight

**Data Cost**: Included with CME subscription

---

## Strategy Adaptation Required

### Instrument Configuration Changes Needed

#### For Bitcoin Futures (MBT):
```python
BITCOIN_CONFIG = {
    "tick_size": 0.50,  # $0.50 per contract
    "point_value": 5,   # $5 per Bitcoin tick
    "margin_per_contract": 1000,  # Approximate
    "trading_hours": "23:00-22:00",  # Almost 24/7
    "max_position": 2,  # Conservative for crypto
    "volatility_multiplier": 3.0,  # 3x more volatile than ES
}
```

#### Key Differences from MES:

| Parameter | MES (Current) | MBT (Bitcoin) | Impact |
|-----------|---------------|---------------|---------|
| **Tick Size** | $1.25 | $0.50 | Smaller moves |
| **Daily Volatility** | ~1.5% | ~5-10% | 3-6x more volatile |
| **Trading Hours** | 23 hrs | 23 hrs | Similar |
| **Gap Risk** | Low | Very High | Weekend gaps 10%+ |
| **VWAP Reliability** | High | Medium | Less institutional |
| **Liquidity** | High | Medium | Wider spreads |

### Signal Generator Modifications

#### Time Filters for Bitcoin
```python
# Bitcoin trades 23/5 - different session management
BITCOIN_SESSIONS = {
    "crypto_asian": "19:00-03:00",   # Lower liquidity
    "crypto_european": "03:00-09:30", # Medium liquidity  
    "crypto_us": "09:30-16:00",     # Best liquidity
    "crypto_evening": "16:00-19:00"  # Medium liquidity
}
```

#### Volatility Adjustments
```python
# Bitcoin needs wider stops due to volatility
def adjust_for_bitcoin(atr, base_stop_multiplier=1.5):
    """Widen stops for Bitcoin's higher volatility"""
    return atr * base_stop_multiplier * 2.0  # 2x wider stops
```

---

## Testing Plan

### Phase 1: Download Bitcoin Data (Free with IBKR)
1. Subscribe to CME Real-Time (covers BTC/MBT)
2. Download 60-90 days MBT 5-minute data
3. Calculate VWAP, ATR, indicators
4. Run backtest with adjusted parameters

### Phase 2: Compare Performance

**Expected Results**:
- **Win Rate**: Lower (30-40% vs 38.9%) due to higher volatility
- **Profit Factor**: Similar (1.8-2.5) if edge persists
- **Trade Frequency**: Higher (2-3x more signals)
- **Drawdowns**: Larger (5-10% vs 2%)

**Acceptable Variance**:
- Win rate ±10%
- Profit factor ±0.5
- Drawdown ±5%

### Phase 3: Validation Criteria

**Strategy Valid if**:
1. Profit Factor > 1.5
2. Positive expectancy (+0.1R per trade minimum)
3. Win rate > 30%
4. Max drawdown < 15%
5. Consistent monthly performance

---

## Implementation Steps

### Step 1: Add Bitcoin Configuration

**File**: `config/instrument_specs.py`

```python
BITCOIN_SPECS = {
    "MBT": {
        "tick_size": 0.50,
        "point_value": 5,
        "margin_per_contract": 1000,
        "trading_hours": {
            "start": "18:00",  # Sunday evening
            "end": "17:00"     # Friday evening
        },
        "timezone": "America/New_York",
        "max_position": 2,
        "risk_multiplier": 2.0,
    }
}
```

### Step 2: Download Bitcoin Data

**Script**: `scripts/download_ibkr_bitcoin.py`

```python
from data.ibkr_provider import IBKRDataProvider

provider = IBKRDataProvider()

# Download Micro Bitcoin (MBT)
df = provider.get_historical_data(
    symbol="MBT",
    duration="3 M",  # 3 months
    bar_size="5 mins",
    use_rth=False    # All hours
)

df.to_csv("data/ibkr/MBT_3month.csv")
```

### Step 3: Calculate Bitcoin Indicators

**Adjustments needed**:
- VWAP reset at 18:00 (crypto session start)
- ATR period: 14 bars (same)
- Volume: Different magnitude (normalize)
- SD bands: May need adjustment for crypto

### Step 4: Run Backtest

```bash
# Test Bitcoin strategy
python scripts/backtest_bitcoin.py \
    --file data/ibkr/MBT_3month.csv \
    --instrument MBT \
    --config config/bitcoin_strategy.yaml
```

---

## Risk Considerations

### Bitcoin-Specific Risks

1. **Weekend Gaps**
   - Bitcoin trades weekends, your strategy doesn't
   - Friday close to Sunday open can be ±20%
   - Solution: Flatten before weekend or reduce size

2. **Extreme Volatility**
   - 10% moves in 5 minutes are common
   - Stop losses can gap through
   - Solution: Wider stops, smaller size

3. **Regulatory Risk**
   - Crypto regulations changing
   - Margin requirements can increase suddenly
   - Solution: Monitor CME notices

4. **Liquidity Issues**
   - Less liquid than ES, especially overnight
   - Slippage can be higher
   - Solution: Trade during US hours only

### Account Size Requirements

**For Bitcoin Testing**:
- Minimum: $25,000 (for proper risk management)
- Recommended: $50,000+ (similar to MES testing)
- Max Position: 2-3 contracts (vs 10 for MES)
- Risk per Trade: 0.5-1% (lower than MES due to volatility)

---

## Alternative: Test on European Markets

### If Bitcoin is too risky, test on:

**FDAX (Germany)**
- Similar to ES (equity index)
- High correlation but different hours
- Lower margin than Bitcoin
- Proven VWAP edge in European markets

**FESX (Euro STOXX 50)**
- European blue chips
- Different volatility profile
- Good diversification test

---

## Recommendation

### Testing Priority

**Option A: Bitcoin (MBT)** ⭐ RECOMMENDED
- **Pros**: High volatility creates more setups, same exchange (CME), free with subscription
- **Cons**: Extreme volatility, high margin, different behavior
- **Timeline**: 1-2 weeks to test
- **Risk**: Medium (paper trade first)

**Option B: European Futures (FDAX/FESX)**
- **Pros**: Similar to ES, different time zone, diversifies portfolio
- **Cons**: Different currency (EUR), separate data subscription needed
- **Timeline**: 2-3 weeks (need Eurex data)
- **Risk**: Low (similar to ES)

**Option C: Commodities (CL/GC)**
- **Pros**: Different asset class, tests strategy robustness
- **Cons**: Very different from equity indices, seasonal factors
- **Timeline**: 2-3 weeks
- **Risk**: High (different patterns)

### My Recommendation: Start with Bitcoin (MBT)

**Why Bitcoin First**:
1. Already available with CME subscription (no extra cost)
2. Tests strategy on different volatility regime
3. Can run parallel to MES (different hours)
4. High activity = faster validation
5. CME regulated (safer than spot crypto)

**Testing Protocol**:
1. Paper trade MBT for 2 weeks (20-30 trades)
2. Compare metrics to MES baseline
3. If PF > 1.5 and win rate > 30%, add to live portfolio
4. Size: Max 1-2 contracts (vs 5-10 for MES)

---

## Quick Start Commands

```bash
# 1. Download Bitcoin data (assuming IBKR connected)
python -c "
from data.ibkr_provider import IBKRDataProvider
p = IBKRDataProvider()
df = p.get_historical_data('MBT', '3 M', '5 mins')
df.to_csv('data/ibkr/MBT_3month.csv')
print(f'Downloaded {len(df)} bars')
"

# 2. Add Bitcoin specs to config
cat >> config/instrument_specs.py << 'EOF'

# Bitcoin Micro Futures (MBT)
INSTRUMENT_SPECS["MBT"] = {
    "tick_size": 0.50,
    "point_value": 5,
    "margin_per_contract": 1000,
    "trading_hours": {"start": "18:00", "end": "17:00"},
    "timezone": "America/New_York",
    "max_position": 2,
    "risk_multiplier": 2.0
}
EOF

# 3. Run backtest
python scripts/backtest_bitcoin.py --file data/ibkr/MBT_3month.csv
```

---

## Expected Timeline

- **Week 1**: Download data, modify configs, run initial backtest
- **Week 2**: Analyze results, optimize parameters
- **Week 3**: Paper trade MBT alongside MES
- **Week 4**: Decision on live trading MBT

**Success Criteria**:
- Profit Factor > 1.5
- Win rate > 30%
- Positive expectancy
- Max drawdown < 15%

---

## Documentation Files Created

1. ✅ `bitcoin_testing_plan.md` - This document
2. Strategy config: `strategy_config_v1.0.md` (stays current for MES)
3. Need to create: `bitcoin_strategy_config.md` (if testing proceeds)

---

**Next Decision Required**:

A) Test on **Bitcoin (MBT)** - High volatility, same exchange
B) Test on **European Futures (FDAX)** - Similar to ES, different hours  
C) Test on **Commodities (CL/GC)** - Different asset class
D) **Skip alternative testing** - Focus on MES live trading

My recommendation: **Option A (Bitcoin)** - Free to test, tests strategy robustness, can run parallel to MES.
