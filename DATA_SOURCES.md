# Extended Data Sources for Backtesting

## Current Limitation
Yahoo Finance limits 5-minute data to 60 days maximum.
Need alternative sources for 6+ months of historical intraday futures data.

## Option 1: Interactive Brokers (IBKR) Historical Data
**Status**: Already integrated, needs market data subscription
**Pros**:
- Up to 1 year of historical data
- Real tick-level data available
- Direct integration with execution system
- Same data source as live trading

**Cons**:
- Requires paid market data subscription (~$20-50/month)
- Must have funded account or pay for data separately
- Rate limits on historical requests

**Implementation**:
```python
# Use existing IBKR provider
from data.ibkr_provider import IBKRDataProvider

provider = IBKRDataProvider()
df = provider.get_historical_data(
    symbol="MES",
    duration="6 M",  # 6 months
    bar_size="5 mins",
    use_rth=False    # Include globex hours
)
```

## Option 2: Polygon.io
**Status**: Not integrated, requires API key
**Pros**:
- Free tier: 5 years of historical data (delayed)
- Paid tier: Real-time + unlimited history
- Clean REST API
- Good documentation

**Cons**:
- Futures data requires paid subscription ($199/month)
- Free tier has 15-minute delay
- Need to write integration code

**Pricing**:
- Free: 5 years historical, 15-min delayed
- Starter ($49/mo): Real-time stocks, delayed futures
- Developer ($199/mo): Real-time futures

**Implementation**:
```python
import polygon

client = polygon.RESTClient("YOUR_API_KEY")
df = client.get_aggs(
    ticker="MES",
    multiplier=5,
    timespan="minute",
    from_="2025-09-01",
    to_="2026-03-20"
)
```

## Option 3: Alpaca Markets
**Status**: Not integrated, requires API key
**Pros**:
- Free historical data (stocks only)
- Good for equity backtesting
- Easy API

**Cons**:
- NO futures data available
- Not suitable for this strategy

## Option 4: Tradovate (Your Original Choice)
**Status**: Not integrated, $25/month API fee
**Pros**:
- Direct futures broker
- Good historical data
- Paper trading available

**Cons**:
- Monthly API fee
- Another integration to build
- Already switched to IBKR to save costs

## Option 5: QuantConnect / QuantRocket
**Status**: Not integrated
**Pros**:
- Institutional-grade data
- Decades of historical data
- Cloud backtesting

**Cons**:
- Expensive ($20-200+/month)
- Overkill for current needs
- Would need to rewrite strategy

## Option 6: Download Data from Multiple Periods
**Status**: Can implement now with Yahoo
**Pros**:
- Free
- No integration needed
- Can stitch together 60-day chunks

**Cons**:
- Manual process
- Gaps between chunks
- Time-consuming

**Implementation**:
```python
# Download overlapping 60-day periods and stitch
periods = [
    ("2025-09-01", "2025-10-31"),
    ("2025-11-01", "2025-12-31"),
    ("2026-01-01", "2026-03-20")
]

for start, end in periods:
    # Download each period separately
    # Save to separate files
    # Concatenate into one large dataframe
```

## Option 7: Norgate Data
**Status**: Not integrated, subscription service
**Pros**:
- Professional-grade futures data
- Years of historical data
- Clean, adjusted data
- Popular with systematic traders

**Cons**:
- Expensive ($395/year for futures)
- Windows only
- Requires separate software

## Recommendation

### Immediate (Free): Option 6 - Stitch Yahoo Data
Download multiple 60-day periods from Yahoo and combine them.
Can get 6+ months by downloading 3 separate chunks.

### Medium-term (Recommended): Option 1 - IBKR Market Data
Subscribe to IBKR market data for futures ($20-40/month).
- Same data as live trading
- Up to 1 year historical
- Already integrated
- Can paper trade immediately

### Long-term: Option 2 - Polygon.io
If you need more than 1 year, Polygon.io at $199/month is best value for serious trading.

## Next Steps

1. **Option 6**: I'll implement a data stitching script to combine multiple 60-day periods from Yahoo
2. **Test on 6+ months**: Validate strategy on extended dataset
3. **If successful**: Subscribe to IBKR market data for live trading

Would you like me to:
A) Implement Option 6 (stitch multiple Yahoo downloads)
B) Help set up IBKR market data subscription
C) Research another data source
