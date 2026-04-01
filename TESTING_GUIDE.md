# Testing the Paper Trading System

This guide explains how to test the paper trading system to ensure everything is working correctly before you start trading.

## Quick Test (No Dependencies)

Run the validation script to check all components:

```bash
python scripts/validate_paper_trading.py
```

For verbose output:

```bash
python scripts/validate_paper_trading.py --verbose
```

This will check:
- ✅ Environment variables
- ✅ Module imports
- ✅ Database connectivity
- ✅ Configuration files
- ✅ Risk Manager functionality
- ✅ Position Sizer calculations
- ✅ Signal Generator (including fallback mode)
- ✅ Circuit Breakers (all 8 breakers)
- ✅ Position Synchronization
- ✅ Webhook Server endpoints

**Expected output:**
```
======================================================================
                    VALIDATION SUMMARY
======================================================================
Checks Passed:   45
Checks Failed:   0
Warnings:        2
Total Checks:    47

✓ ALL CHECKS PASSED!

Your paper trading system is ready to use!
```

## Running Pytest Tests

If you have pytest installed, run the full test suite:

### Install pytest (if not already installed):

```bash
pip install pytest pytest-asyncio httpx
```

### Run all tests:

```bash
# Using the test runner script
python scripts/run_tests.py

# Or run directly with pytest
pytest tests/ -v
```

### Run specific test categories:

```bash
# Unit tests only
pytest tests/test_risk_manager.py tests/test_position_sizer.py -v

# Integration tests only
pytest tests/integration/test_paper_trading.py -v

# Specific test
pytest tests/integration/test_paper_trading.py::TestPaperTradingWorkflow::test_complete_long_trade -v
```

## Manual Testing with curl

### 1. Start the Webhook Server

```bash
uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000
```

You should see:
```
[WebhookServer] Starting up...
[WebhookServer] Position sync result: SYNCED
[WebhookServer] Circuit breakers initialized: 8 breakers
[WebhookServer] Ready for connections
```

### 2. Test Health Endpoint

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### 3. Check System Status

```bash
curl http://localhost:8000/status
```

Expected response includes:
- Position status
- Circuit breaker states
- Recent trades
- System status

### 4. Test Entry Signal

```bash
curl -X POST http://localhost:8000/webhook/entry \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "MES1!",
    "action": "buy",
    "orderType": "market",
    "quantity": 1,
    "price": "5000.00",
    "timestamp": "2024-01-15T10:30:00Z",
    "setup": "MEAN_REVERSION_LONG",
    "stopPrice": "4995.00",
    "targetPrice": "5010.00"
  }'
```

Expected response:
```json
{
  "status": "received",
  "order": {
    "ticker": "MES1!",
    "action": "BUY",
    "quantity": 1,
    ...
  },
  "message": "Entry signal received and validated"
}
```

### 5. Check Position

```bash
curl http://localhost:8000/position
```

### 6. Test Exit Signal

```bash
curl -X POST http://localhost:8000/webhook/exit \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "MES1!",
    "action": "exit",
    "quantity": 1,
    "price": "5010.00",
    "timestamp": "2024-01-15T10:45:00Z",
    "reason": "target"
  }'
```

Expected response:
```json
{
  "status": "received",
  "exit": { ... },
  "pnl": {
    "gross": 50.0,
    "commission": 1.4,
    "net": 48.6
  }
}
```

## Testing Circuit Breakers

### Test Daily Loss Limit

1. Simulate a large daily loss:
```bash
# This would require setting daily_pnl in the circuit breaker context
# The system will automatically block new trades
```

2. Try to enter a trade - it should be blocked with:
```json
{
  "status": "blocked",
  "reason": "daily_loss: Daily loss $900 >= limit $800"
}
```

### Test MLL Proximity

When equity gets within 10% of the MLL floor ($47,500), the system should block trades.

### Test Consecutive Losses

After 3 consecutive losing trades, the system should block further trading.

## Complete Workflow Test

Run the comprehensive integration test:

```bash
pytest tests/integration/test_paper_trading.py::TestPaperTradingWorkflow::test_complete_long_trade -v -s
```

This test:
1. Checks initial system status
2. Sends entry signal
3. Verifies trade is logged
4. Simulates broker fill
5. Checks position status
6. Sends exit signal
7. Verifies PnL calculation
8. Simulates position close
9. Final status check

Expected output:
```
[INTEGRATION TEST] Complete Long Trade Workflow
======================================================================

1. Checking initial system status...
   ✓ System ready for trading

2. Sending entry signal (BUY 1 MES @ 5000)...
   ✓ Entry signal accepted: {...}

3. Verifying trade in database...
   ✓ Trade logged: ID=1

4. Simulating broker fill...

5. Checking position status...
   ✓ Position: {...}

6. Sending exit signal (SELL 1 MES @ 5010)...

7. Verifying PnL calculation...
   ✓ PnL: Gross=$50.0, Commission=$1.4, Net=$48.6

8. Simulating broker position closed...

9. Final system status...
   ✓ Trade completed successfully

======================================================================
INTEGRATION TEST PASSED ✓
======================================================================
```

## Database Verification

Check that trades are being recorded:

```bash
# Using sqlite3 command line
sqlite3 database/trading_analysis.db

# Then in sqlite3:
SELECT * FROM live_trades ORDER BY entry_time DESC LIMIT 5;
SELECT * FROM daily_account_summary ORDER BY date DESC LIMIT 5;
.quit
```

## Log Verification

Check webhook logs:

```bash
# View recent webhooks
cat logs/webhooks_$(date +%Y%m%d).jsonl | tail -10

# Pretty print JSON
jq . logs/webhooks_$(date +%Y%m%d).jsonl | tail -100
```

## Troubleshooting Common Issues

### Issue: "Module not found"

**Solution:**
```bash
# Make sure you're in the project root
cd /path/to/FuturesTraderTool

# Install dependencies
pip install -r requirements.txt
```

### Issue: "Database locked"

**Solution:**
```bash
# Check if multiple processes are accessing the DB
lsof database/trading_analysis.db

# Kill any hanging processes
pkill -f "uvicorn\|python.*poller"

# Restart the webhook server
```

### Issue: "Circuit breaker OPEN"

**Solution:**
```bash
# Check which breaker is open
curl http://localhost:8000/status | jq '.circuit_breakers'

# Reset manually (with admin secret)
curl -X POST "http://localhost:8000/admin/reset-breaker?breaker_name=daily_loss" \
  -H "X-Secret: your_admin_secret"
```

### Issue: "Already in position"

**Solution:**
```bash
# Check current position
curl http://localhost:8000/position

# If flat but still blocked, sync positions
curl http://localhost:8000/status

# Or emergency flatten
curl -X POST "http://localhost:8000/admin/emergency-flatten?reason=testing" \
  -H "X-Secret: your_admin_secret"
```

### Issue: Tests failing with "no module named X"

**Solution:**
```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Make sure you're in the project root when running tests
cd /path/to/FuturesTraderTool
pytest tests/ -v
```

## Expected Test Results

### All unit tests should pass:
- test_risk_manager.py: 15 tests
- test_position_sizer.py: 20 tests  
- test_signal_generator.py: 15 tests
- test_signal_generator_enhanced.py: 18 tests
- test_prop_firm_simulator.py: 12 tests
- test_vwap_calculator.py: 10 tests
- test_position_sync.py: 12 tests
- test_circuit_breakers.py: 25 tests

**Total: ~127 unit tests**

### Integration tests should pass:
- test_paper_trading.py: 12 tests
  - test_complete_long_trade
  - test_short_trade_workflow
  - test_multiple_trades_respect_daily_limit
  - test_entry_blocked_by_daily_loss_limit
  - test_entry_blocked_by_mll_proximity
  - test_entry_blocked_by_consecutive_losses
  - test_data_freshness_circuit_breaker
  - test_broker_connectivity_circuit_breaker
  - test_order_rate_circuit_breaker
  - And more...

## Performance Testing

Test system under load:

```bash
# Install Apache Bench (ab) or use curl in a loop

# Send 20 entry signals rapidly
for i in {1..20}; do
  curl -X POST http://localhost:8000/webhook/entry \
    -H "Content-Type: application/json" \
    -d "{\"ticker\":\"MES1!\",\"action\":\"buy\",\"quantity\":1,\"price\":\"5000.00\",\"timestamp\":\"$(date -Iseconds)\"}" &
done
wait

# Check that order rate limiter blocked excess orders
curl http://localhost:8000/status | jq '.system_status'
```

## Next Steps After Testing

Once all tests pass:

1. **Configure TradingView** (see PAPER_TRADING_QUICK_START.md)
2. **Start paper trading** with small size
3. **Monitor for 1-2 days** to ensure stability
4. **Gradually increase** position size after proven successful

## Support

If tests are failing:
1. Run validation script: `python scripts/validate_paper_trading.py --verbose`
2. Check logs in `logs/` directory
3. Verify all environment variables are set
4. Ensure database is initialized: `python scripts/setup_demo_account.py`

Happy testing! 🧪
