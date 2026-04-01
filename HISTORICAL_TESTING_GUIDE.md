# Historical Data Testing Guide
## Test Your Strategy on Real Market Data

This guide shows you how to backtest the fallback strategy on historical data **BEFORE** risking real money.

---

## 🎯 WHY TEST ON HISTORICAL DATA?

**Benefits:**
- ✅ See how strategy performs on real market conditions
- ✅ Validate 55-65% win rate claim
- ✅ Identify best/worst times to trade
- ✅ Build confidence before live trading
- ✅ No risk - test on past data

**What you'll learn:**
- Actual win rate on recent data
- Which setups work best
- Optimal risk parameters
- Expected drawdowns

---

## 🚀 QUICK START (3 COMMANDS)

### **Step 1: Ensure TWS is running**
```bash
# TWS must be running with API enabled
# You already did this - just keep it running
```

### **Step 2: Run backtest**
```bash
# Test last 30 days on MES
py scripts/backtest_fallback.py --symbol MES --days 30

# Test last 60 days on MNQ
py scripts/backtest_fallback.py --symbol MNQ --days 60

# Test with different bar size
py scripts/backtest_fallback.py --symbol MES --days 30 --bar-size "15 mins"
```

### **Step 3: Review results**
The script will output:
- Total trades taken
- Win rate achieved
- Profit/loss summary
- Setup-by-setup breakdown
- Drawdown analysis

---

## 📊 EXAMPLE OUTPUT

```
======================================================================
                    BACKTEST RESULTS - FALLBACK STRATEGY
======================================================================

OVERALL PERFORMANCE:
----------------------------------------------------------------------
  Total Trades:        28
  Win Rate:            57.1%
  Total PnL:           $847.50
  Profit Factor:       1.68
  Average R-Multiple:  0.42R
  Max Drawdown:        -$320.00

TRADE BREAKDOWN:
----------------------------------------------------------------------
  Winning Trades:      16 (57.1%)
  Losing Trades:       12
  Average Win:         $112.50
  Average Loss:        -$65.20

SETUP PERFORMANCE:
----------------------------------------------------------------------
  MEAN_REVERSION_LONG:   8 trades, +$320.00, 62.5% win rate
  MEAN_REVERSION_SHORT:  7 trades, +$280.50, 57.1% win rate
  SD2_EXTREME_FADE:      6 trades, +$180.00, 50.0% win rate
  VWAP_CONTINUATION:     7 trades, +$67.00, 57.1% win rate

✅ RESULT: Win rate within expected range (55-65%)
✅ Strategy is ready for live paper trading!
```

---

## 🧪 COMPREHENSIVE TESTING PLAN

### **Test 1: Different Time Periods**

```bash
# Test 1 month
py scripts/backtest_fallback.py --days 30

# Test 3 months
py scripts/backtest_fallback.py --days 90

# Test during volatile period (check what month)
py scripts/backtest_fallback.py --days 30
```

**What to look for:**
- Consistent win rate across periods (50-65%)
- No catastrophic drawdowns
- All setups showing positive expectancy

### **Test 2: Different Bar Sizes**

```bash
# 1-minute bars (most signals)
py scripts/backtest_fallback.py --days 14 --bar-size "1 min"

# 5-minute bars (balanced)
py scripts/backtest_fallback.py --days 30 --bar-size "5 mins"

# 15-minute bars (fewer signals, higher quality)
py scripts/backtest_fallback.py --days 60 --bar-size "15 mins"
```

**What to look for:**
- 5-min bars: Sweet spot for day trading
- Too many signals on 1-min = noise
- 15-min may miss good entries

### **Test 3: Different Instruments**

```bash
# MES (Micro E-mini S&P 500)
py scripts/backtest_fallback.py --symbol MES --days 30

# MNQ (Micro E-mini Nasdaq)
py scripts/backtest_fallback.py --symbol MNQ --days 30

# ES (E-mini S&P 500)
py scripts/backtest_fallback.py --symbol ES --days 30
```

**What to look for:**
- MES should work best (most liquid)
- MNQ may be more volatile
- Adjust position sizes accordingly

### **Test 4: Walk-Forward Analysis**

Test consecutive periods to see consistency:

```bash
# Month 1
py scripts/backtest_fallback.py --days 30

# Month 2
py scripts/backtest_fallback.py --days 30

# Month 3
py scripts/backtest_fallback.py --days 30
```

**What to look for:**
- Win rate consistent month-to-month
- No "blow up" months
- Strategy robust across conditions

---

## 📈 INTERPRETING RESULTS

### **✅ GOOD SIGNS:**

- **Win rate 50-65%** - Matches expected range
- **Profit factor > 1.3** - Making more than losing
- **Max drawdown < 15%** - Manageable risk
- **Positive expectancy** - Strategy is profitable
- **All setups profitable** - Diversified edge

### **⚠️ WARNING SIGNS:**

- **Win rate < 45%** - May need adjustment
- **Max drawdown > 20%** - Too risky
- **One setup dominates** - Over-reliance
- **Consecutive losing months** - Strategy broken

### **❌ BAD SIGNS:**

- **Win rate < 40%** - Strategy not working
- **Negative expectancy** - Losing money
- **Profit factor < 1.0** - More losses than wins
- **Huge drawdowns** - Risk too high

---

## 🎓 ADVANCED TESTING

### **Test with Different Risk Parameters**

Modify the backtest script to test:

```python
# Test with tighter stops
RISK_CONFIG = {
    "max_daily_trades": 3,  # More selective
    "max_risk_per_trade_pct": 0.005,  # 0.5% instead of 1%
}

# Or looser
RISK_CONFIG = {
    "max_daily_trades": 10,  # More trades
    "max_risk_per_trade_pct": 0.02,  # 2% instead of 1%
}
```

### **Test Different Time Windows**

```bash
# Only morning session
py scripts/backtest_fallback.py --days 30
# (Modify script to only trade 9:45-11:30)

# Only afternoon session
py scripts/backtest_fallback.py --days 30
# (Modify script to only trade 13:30-15:45)
```

### **Monte Carlo Simulation**

Run multiple backtests and randomize trade order to see robustness:

```bash
# Run 10 backtests on different periods
for i in {1..10}; do
    py scripts/backtest_fallback.py --days 30
done
```

---

## ✅ VALIDATION CHECKLIST

Before going live, verify:

- [ ] **Win rate 50-65%** across multiple time periods
- [ ] **Profit factor > 1.3** consistently
- [ ] **Max drawdown < 15%** in worst period
- [ ] **All 3 setups profitable** (diversification)
- [ ] **Positive expectancy** on all tests
- [ ] **Works on MES** (your primary instrument)
- [ ] **5-minute bars** optimal (not too many/few signals)
- [ ] **Consistent performance** month-to-month

---

## 🚨 WHEN TO STOP TESTING

**Stop and DON'T trade live if:**
- Win rate consistently < 45%
- Max drawdown > 20%
- Negative expectancy
- Strategy fails across multiple periods

**Instead:**
- Review signal conditions
- Adjust risk parameters
- Wait for video data to refine
- Test on different instruments

---

## 🎯 NEXT STEPS

**After successful backtesting:**

1. ✅ **Run 3-5 backtests** on different periods
2. ✅ **Verify consistency** across all tests
3. ✅ **Start paper trading** with 1 contract
4. ✅ **Log first 30 live trades**
5. ✅ **Compare live vs backtest** results
6. ✅ **Switch to VIDEO_DERIVED** mode

**Timeline:**
- Backtesting: 1-2 days
- Paper trading: 2-4 weeks
- Video mode activation: After 30 trades

---

## 💡 PRO TIPS

1. **Don't over-optimize** - Strategy should work across periods
2. **Test during volatile times** - See worst-case performance
3. **Test during calm times** - See normal performance
4. **Use 5-min bars** - Sweet spot for day trading
5. **Focus on MES** - Most liquid, best fills

---

## 📞 TROUBLESHOOTING

**"No data returned"**
- Check TWS is running
- Verify market is open (RTH hours)
- Try different symbol

**"All trades are losses"**
- Check date range (avoid bear markets if testing long-only)
- Verify stop/target levels reasonable
- Review signal conditions

**"Too few trades"**
- Expand date range
- Check time filters not too restrictive
- Verify VWAP calculation working

---

## 🎉 YOU'RE READY!

Run your first backtest:
```bash
py scripts/backtest_fallback.py --symbol MES --days 30
```

**Good luck! May your win rate be high and your drawdowns low!** 📈
