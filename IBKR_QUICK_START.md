"""
Interactive Brokers Quick Start Guide
======================================

100% FREE Paper Trading - No API fees!

STEP 1: Download & Install TWS (Trader Workstation)
----------------------------------------------------
1. Go to: https://www.interactivebrokers.com/en/index.php?f=16457
2. Click "Download TWS"
3. Install the software
4. Login with paper trading credentials
   (Create account if you don't have one)

STEP 2: Enable API in TWS
--------------------------
1. In TWS, click: Edit → Global Configuration
2. Click: API → Settings (left sidebar)
3. Check these boxes:
   ✓ Enable "ActiveX and Socket Clients"
   ✓ Socket port: 7497 (for paper trading)
   ✓ Uncheck "Read-Only API"
4. Click OK
5. Restart TWS

STEP 3: Install Python Package
-------------------------------
pip install ib_insync

STEP 4: Create .env File
-------------------------
Create a file named ".env" in your project folder:

IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
DB_PATH=./database/trading_analysis.db
PROP_FIRM=TOPSTEP_50K
TV_WEBHOOK_SECRET=any_random_string_here
ADMIN_SECRET=another_random_string_here

STEP 5: Run Setup Script
-------------------------
python scripts/setup_ibkr_account.py

STEP 6: Start Trading!
----------------------
Terminal 1 - Make sure TWS is running

Terminal 2 - Start webhook server:
    uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000

Terminal 3 - Test the connection:
    curl http://localhost:8000/health

STEP 7: Send Test Trade
------------------------
curl -X POST http://localhost:8000/webhook/entry \
  -H "Content-Type: application/json" \
  -d '{"ticker":"MES1!","action":"buy","quantity":1,"price":"5000.00","timestamp":"2024-01-15T10:30:00Z","setup":"MEAN_REVERSION_LONG"}'

STEP 8: Configure TradingView
------------------------------
Webhook URL: http://localhost:8000/webhook/entry

Alert Message for Long Entry:
{"ticker":"{{ticker}}","action":"buy","quantity":1,"price":"{{close}}","timestamp":"{{time}}","setup":"mean_reversion_long"}

TROUBLESHOOTING
----------------

Issue: "Cannot connect to TWS"
Solution:
  1. Check TWS is running
  2. Verify API is enabled on port 7497
  3. Check firewall settings
  4. Try restarting TWS

Issue: "No data returned"
Solution:
  - Markets may be closed (try during RTH: 9:30 AM - 4:00 PM ET)
  - Check if you're subscribed to market data in TWS

Issue: "Module not found: ib_insync"
Solution:
  pip install ib_insync

BENEFITS OF IBKR
-----------------
✓ 100% FREE - No monthly API fees
✓ Real-time data included
✓ Paper trading with $1M+ virtual cash
✓ All futures markets (MES, MNQ, ES, NQ, CL, GC, etc.)
✓ Excellent execution quality
✓ Works with Python (ib_insync)

LIMITATIONS
------------
- Need to keep TWS running (can use IB Gateway instead)
- Slightly more complex setup than Tradovate
- Windows/Mac only (TWS software)

ALTERNATIVE: IB Gateway
------------------------
For 24/7 automated trading without TWS GUI:
1. Download IB Gateway (lighter than TWS)
2. Use port 4001 (paper) or 4002 (live)
3. Everything else works the same

SUPPORT
--------
Interactive Brokers support:
- Phone: (312) 765-7268
- Chat: Available in TWS
- Docs: https://interactivebrokers.github.io/tws-api/

Ready to trade? Run: python scripts/setup_ibkr_account.py
"""
