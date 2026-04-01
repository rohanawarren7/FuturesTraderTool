"""
Paper Trading Quick Start Guide
================================

This guide will walk you through setting up and running the paper trading system.

PREREQUISITES
-------------
1. Python 3.11+ installed
2. Tradovate demo account created (https://www.tradovate.com/)
3. All dependencies installed: pip install -r requirements.txt

STEP 1: Environment Setup
-------------------------
Create a .env file in the project root:

    TRADOVATE_USERNAME=your_tradovate_username
    TRADOVATE_PASSWORD=your_tradovate_password
    TRADOVATE_APP_ID=your_app_id
    TRADOVATE_USE_DEMO=true
    DB_PATH=./database/trading_analysis.db
    PROP_FIRM=TOPSTEP_50K
    TV_WEBHOOK_SECRET=your_webhook_secret
    ADMIN_SECRET=your_admin_secret

To get your Tradovate App ID:
1. Log in to Tradovate
2. Go to Settings → API Access
3. Create a new app or use existing one
4. Copy the App ID

STEP 2: Run Setup Script
------------------------
    python scripts/setup_demo_account.py

This will:
- Verify your environment variables
- Initialize the database
- Connect to your Tradovate demo account
- Configure Topstep 50K constraints
- Create initial daily summary

STEP 3: Start the Webhook Server
--------------------------------
Terminal 1:
    uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000

This starts the server that will receive signals from TradingView.

Verify it's running:
    curl http://localhost:8000/health

You should see: {"status": "ok", ...}

STEP 4: Start the Position Poller (Optional)
--------------------------------------------
Terminal 2:
    python execution/tradovate_poller.py

This polls Tradovate for position updates and fills.

STEP 5: Test the System
-----------------------
Before connecting TradingView, let's test manually:

1. Check system status:
   curl http://localhost:8000/status

2. Send a test entry signal:
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

3. Check the trade was logged:
   curl http://localhost:8000/status

4. Send an exit signal:
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

STEP 6: Configure TradingView
-----------------------------
1. Open TradingView
2. Load the VWAP_Bot_Strategy indicator
3. Right-click on chart → Add Alert
4. Condition: VWAP Bot Strategy → Long Entry
5. Message:
   {
     "ticker": "{{ticker}}",
     "action": "buy",
     "orderType": "market",
     "quantity": 1,
     "price": "{{close}}",
     "timestamp": "{{time}}",
     "setup": "mean_reversion",
     "stopPrice": "{{plot_2}}",
     "targetPrice": "{{plot_0}}"
   }
6. Webhook URL: http://localhost:8000/webhook/entry
7. Create alert

Repeat for Short Entry (action: "sell") and Exit signals.

STEP 7: Monitor Your Trades
---------------------------
- System status: http://localhost:8000/status
- Position: http://localhost:8000/position
- Database: Check trading_analysis.db
- Logs: Check logs/webhooks_YYYYMMDD.jsonl

STEP 8: Emergency Procedures
-----------------------------
If something goes wrong:

1. Emergency flatten all positions:
   curl -X POST http://localhost:8000/admin/emergency-flatten \
     -H "X-Secret: your_admin_secret" \
     -d "reason=Manual+emergency+stop"

2. Reset a circuit breaker:
   curl -X POST "http://localhost:8000/admin/reset-breaker?breaker_name=daily_loss" \
     -H "X-Secret: your_admin_secret"

3. Check circuit breaker status:
   curl http://localhost:8000/status | grep -A 20 circuit_breakers

TROUBLESHOOTING
---------------

Issue: "Failed to connect to Tradovate"
Solution: Check your credentials in .env file

Issue: "Database locked"
Solution: Only run one instance of the poller

Issue: "Circuit breaker OPEN"
Solution: Check which breaker triggered at /status endpoint

Issue: "Already in position"
Solution: Position sync issue - check /position endpoint

Issue: TradingView webhook not received
Solution: 
  - Ensure webhook server is accessible from internet (use ngrok for testing)
  - Check webhook URL is correct
  - Verify TV_WEBHOOK_SECRET matches

SAFETY FEATURES
---------------
The system includes multiple safety features:

1. Max 5 trades per day
2. Stop after 3 consecutive losses
3. Daily loss limit: $800 (40% of MLL)
4. MLL proximity alert (within 10% of floor)
5. No trading first/last 15 minutes
6. Data freshness monitoring
7. Broker connectivity checks
8. Order rate limiting
9. Position synchronization
10. Emergency flatten capability

TESTING
-------
Run the integration tests:
    pytest tests/integration/test_paper_trading.py -v

Run a specific test:
    pytest tests/integration/test_paper_trading.py::TestPaperTradingWorkflow::test_complete_long_trade -v

NEXT STEPS
----------
Once paper trading is stable:
1. Process video data from The Ark
2. Wait for Sierra Chart setup (Week 2)
3. Gradually increase position size
4. After 30 days profitable, consider live capital

SUPPORT
-------
For issues:
1. Check logs in logs/ directory
2. Review test output
3. Verify all environment variables
4. Check Tradovate API status

Happy paper trading! 🚀
"""
