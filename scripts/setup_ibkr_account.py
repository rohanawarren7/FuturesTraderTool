"""
Interactive Brokers Setup Script
================================
Sets up IBKR paper trading account for FREE trading.

Usage:
    python scripts/setup_ibkr_account.py
    
Prerequisites:
    1. Download and install TWS (Trader Workstation)
       https://www.interactivebrokers.com/en/index.php?f=16457
       
    2. Enable API in TWS:
       Edit → Global Configuration → API → Settings
       ✓ Enable "ActiveX and Socket Clients"
       ✓ Socket port: 7497 (paper trading)
       ✓ Uncheck "Read-Only API"
       
    3. Install ib_insync:
       pip install ib_insync
"""

import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


def print_header(text):
    """Print a formatted header."""
    print("\n" + "="*70)
    print(text.center(70))
    print("="*70 + "\n")


def check_tws_running():
    """Check if TWS is running and accessible."""
    try:
        from data.ibkr_provider import IBKRDataProvider
        
        print("[1/5] Checking if TWS is running...")
        provider = IBKRDataProvider(host='127.0.0.1', port=7497)
        
        if provider.connect():
            print("✓ TWS is running and accepting connections")
            provider.disconnect()
            return True
        else:
            print("✗ Cannot connect to TWS")
            print("\nPlease ensure:")
            print("  1. TWS is running")
            print("  2. API is enabled on port 7497")
            print("  3. 'ActiveX and Socket Clients' is checked")
            return False
            
    except Exception as e:
        print(f"✗ Error connecting to TWS: {e}")
        return False


def check_environment():
    """Check environment variables."""
    print("[2/5] Checking environment configuration...")
    
    required_vars = [
        'DB_PATH',
    ]
    
    optional_vars = [
        'IBKR_HOST',
        'IBKR_PORT',
        'IBKR_CLIENT_ID',
        'PROP_FIRM',
    ]
    
    all_good = True
    
    # Check required
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"✓ {var}: {value}")
        else:
            print(f"✗ {var}: NOT SET (Required)")
            all_good = False
    
    # Check optional (show defaults)
    for var in optional_vars:
        value = os.getenv(var)
        if value:
            print(f"✓ {var}: {value}")
        else:
            default = {
                'IBKR_HOST': '127.0.0.1',
                'IBKR_PORT': '7497',
                'IBKR_CLIENT_ID': '1',
                'PROP_FIRM': 'TOPSTEP_50K'
            }.get(var, 'Not set')
            print(f"⚠ {var}: Using default ({default})")
    
    return all_good


def initialize_database():
    """Initialize database for IBKR trading."""
    print("\n[3/5] Initializing database...")
    
    try:
        from database.db_manager import DBManager
        
        db_path = os.getenv('DB_PATH', './database/trading_analysis.db')
        db = DBManager(db_path)
        
        print(f"✓ Database initialized: {db_path}")
        
        # Create initial daily summary for paper trading
        from config.prop_firm_configs import PROP_FIRM_CONFIGS
        
        config = PROP_FIRM_CONFIGS.get(os.getenv('PROP_FIRM', 'TOPSTEP_50K'))
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        try:
            db.upsert_daily_summary({
                'date': today,
                'prop_firm': os.getenv('PROP_FIRM', 'TOPSTEP_50K'),
                'account_id': 'IBKR_PAPER',
                'opening_balance': config['account_size'],
                'closing_balance': config['account_size'],
                'daily_pnl': 0.0,
                'total_trades': 0,
                'winning_trades': 0,
                'mll_floor': config['account_size'] - config['max_loss_limit'],
                'peak_eod_balance': config['account_size'],
                'winning_days_since_payout': 0,
                'payout_taken': 0,
                'status': 'PAPER_TRADING'
            })
            print(f"✓ Daily summary created for {today}")
        except Exception as e:
            print(f"⚠ Could not create daily summary: {e}")
        
        return True
        
    except Exception as e:
        print(f"✗ Database error: {e}")
        return False


def test_data_connection():
    """Test data connection to IBKR."""
    print("\n[4/5] Testing data connection...")
    
    try:
        from data.ibkr_provider import IBKRDataProvider
        
        provider = IBKRDataProvider.from_env()
        
        if not provider.connect():
            print("✗ Failed to connect")
            return False
        
        print("✓ Connected to IBKR")
        
        # Test getting account info
        account = provider.get_account_summary()
        if account:
            print(f"✓ Account connected: {account.get('account', 'Unknown')}")
            print(f"  Net Liquidation: ${account.get('NetLiquidation', 0):,.2f}")
        
        # Test getting positions
        positions = provider.get_positions()
        print(f"✓ Positions retrieved: {len(positions)} open positions")
        
        # Test getting historical data
        print("\n  Testing historical data fetch (MES 5-min bars)...")
        df = provider.get_historical_data('MES', duration='1 D', bar_size='5 mins')
        
        if not df.empty:
            print(f"✓ Historical data: {len(df)} bars retrieved")
            print(f"  Latest bar: {df.iloc[-1]['close']:.2f}")
        else:
            print("⚠ No historical data (market may be closed)")
        
        provider.disconnect()
        return True
        
    except Exception as e:
        print(f"✗ Data connection error: {e}")
        return False


def print_configuration():
    """Print current configuration."""
    print("\n[5/5] Configuration Summary")
    print("-" * 70)
    
    from config.prop_firm_configs import PROP_FIRM_CONFIGS
    from config.bot_risk_params import BOT_RISK_PARAMS
    
    config = PROP_FIRM_CONFIGS.get(os.getenv('PROP_FIRM', 'TOPSTEP_50K'))
    
    print("\nInteractive Brokers (TWS):")
    print(f"  Host: {os.getenv('IBKR_HOST', '127.0.0.1')}")
    print(f"  Port: {os.getenv('IBKR_PORT', '7497')} (Paper Trading)")
    print(f"  Client ID: {os.getenv('IBKR_CLIENT_ID', '1')}")
    
    print("\nProp Firm Configuration:")
    print(f"  Firm: {os.getenv('PROP_FIRM', 'TOPSTEP_50K')}")
    print(f"  Account Size: ${config['account_size']:,}")
    print(f"  Profit Target: ${config['profit_target']:,}")
    print(f"  Max Loss Limit: ${config['max_loss_limit']:,}")
    print(f"  Max Contracts: {config['max_contracts']}")
    
    print("\nRisk Parameters:")
    print(f"  Max Daily Trades: {BOT_RISK_PARAMS['max_daily_trades']}")
    print(f"  Max Consecutive Losses: {BOT_RISK_PARAMS['max_consecutive_losses']}")
    print(f"  Max Risk Per Trade: {BOT_RISK_PARAMS['max_risk_per_trade_pct']*100:.0f}%")
    
    print("\nDatabase:")
    print(f"  Path: {os.getenv('DB_PATH', './database/trading_analysis.db')}")


def create_env_template():
    """Create .env template file."""
    env_path = Path('.env')
    
    if env_path.exists():
        print(f"\n.env file already exists at {env_path.absolute()}")
        return
    
    template = """# VWAP Trading Bot - Interactive Brokers Configuration
# FREE Paper Trading Setup

# Interactive Brokers TWS Settings
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1

# Database
DB_PATH=./database/trading_analysis.db

# Prop Firm Configuration
PROP_FIRM=TOPSTEP_50K

# Webhook & Admin Secrets (make up any random strings)
TV_WEBHOOK_SECRET=your_webhook_secret_here
ADMIN_SECRET=your_admin_secret_here

# Optional: Gemini API for video analysis
# GEMINI_API_KEY=your_gemini_api_key

# Optional: Logging
LOG_LEVEL=INFO
"""
    
    with open(env_path, 'w') as f:
        f.write(template)
    
    print(f"\n✓ Created .env template at {env_path.absolute()}")
    print("  Please review and update if needed")


def main():
    """Main setup function."""
    print_header("INTERACTIVE BROKERS (IBKR) PAPER TRADING SETUP")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\nThis setup is 100% FREE - no API fees!")
    print("-" * 70)
    
    # Check prerequisites
    print("\nPrerequisites:")
    print("  1. Download TWS from: https://www.interactivebrokers.com")
    print("  2. Install and login with paper trading account")
    print("  3. Enable API: Edit → Global Config → API → Settings")
    print("     - Enable 'ActiveX and Socket Clients'")
    print("     - Set port to 7497")
    print("     - Uncheck 'Read-Only API'")
    print("  4. Install ib_insync: pip install ib_insync")
    
    input("\nPress Enter when ready...")
    
    # Run checks
    checks = []
    
    checks.append(("TWS Running", check_tws_running()))
    checks.append(("Environment", check_environment()))
    checks.append(("Database", initialize_database()))
    checks.append(("Data Connection", test_data_connection()))
    
    # Print config
    print_configuration()
    
    # Create env template if needed
    if not Path('.env').exists():
        create_env_template()
    
    # Summary
    print_header("SETUP SUMMARY")
    
    passed = sum(1 for _, result in checks if result)
    total = len(checks)
    
    for check_name, result in checks:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"{status}: {check_name}")
    
    print(f"\nResults: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n" + "="*70)
        print("✓ ALL CHECKS PASSED!")
        print("="*70)
        print("\nYour IBKR paper trading system is ready!")
        print("\nNext steps:")
        print("  1. Start TWS and login to paper account")
        print("  2. Run validation: python scripts/validate_paper_trading.py")
        print("  3. Start webhook server:")
        print("     uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000")
        print("  4. Start trading!")
        return 0
    else:
        print("\n" + "="*70)
        print("✗ SETUP INCOMPLETE")
        print("="*70)
        print("\nPlease fix the failed checks above.")
        print("\nCommon issues:")
        print("  - TWS not running: Start TWS and login")
        print("  - API not enabled: Check TWS settings")
        print("  - Wrong port: Ensure port 7497 is set")
        return 1


if __name__ == "__main__":
    sys.exit(main())
