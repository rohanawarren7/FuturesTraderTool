#!/usr/bin/env python3
"""
Paper Trading System Validation Script
=====================================

This script validates that all components of the paper trading system are working correctly.
Run this before starting live paper trading.

Usage:
    python scripts/validate_paper_trading.py
    
Or with verbose output:
    python scripts/validate_paper_trading.py --verbose
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import argparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header(text):
    """Print a formatted header."""
    print(f"\n{Colors.BOLD}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{text.center(70)}{Colors.END}")
    print(f"{Colors.BOLD}{'='*70}{Colors.END}\n")


def print_success(text):
    """Print success message."""
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text):
    """Print error message."""
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_warning(text):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_info(text):
    """Print info message."""
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")


class PaperTradingValidator:
    """Validates the paper trading system."""
    
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.checks_passed = 0
        self.checks_failed = 0
        self.checks_warnings = 0
        
    def run_all_checks(self):
        """Run all validation checks."""
        print_header("PAPER TRADING SYSTEM VALIDATION")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Run all check categories
        self.check_environment()
        self.check_imports()
        self.check_database()
        self.check_configuration()
        self.check_risk_manager()
        self.check_position_sizer()
        self.check_signal_generator()
        self.check_circuit_breakers()
        self.check_position_sync()
        self.check_webhook_server()
        
        # Print summary
        self.print_summary()
        
    def check_environment(self):
        """Check environment variables."""
        print_header("1. ENVIRONMENT VARIABLES")
        
        required_vars = [
            'TRADOVATE_USERNAME',
            'TRADOVATE_PASSWORD',
            'TRADOVATE_APP_ID',
        ]
        
        optional_vars = [
            'DB_PATH',
            'PROP_FIRM',
            'TV_WEBHOOK_SECRET',
            'ADMIN_SECRET',
        ]
        
        all_present = True
        for var in required_vars:
            value = os.getenv(var)
            if value:
                if self.verbose:
                    print_success(f"{var}: {value[:3]}...{value[-3:] if len(value) > 6 else ''}")
                else:
                    print_success(f"{var}: Set")
                self.checks_passed += 1
            else:
                print_error(f"{var}: Not set (REQUIRED)")
                all_present = False
                self.checks_failed += 1
        
        for var in optional_vars:
            value = os.getenv(var)
            if value:
                if self.verbose:
                    print_success(f"{var}: {value}")
                else:
                    print_success(f"{var}: Set")
                self.checks_passed += 1
            else:
                print_warning(f"{var}: Not set (optional)")
                self.checks_warnings += 1
        
        if not all_present:
            print()
            print_error("Missing required environment variables!")
            print_info("Create a .env file or set these variables in your shell.")
            
    def check_imports(self):
        """Check that all modules can be imported."""
        print_header("2. MODULE IMPORTS")
        
        modules_to_test = [
            ('core.risk_manager', 'RiskManager'),
            ('core.position_sizer', 'PositionSizer'),
            ('core.signal_generator', 'SignalGenerator'),
            ('core.vwap_calculator', 'VWAPCalculator'),
            ('core.market_state_detector', 'MarketStateDetector'),
            ('execution.position_sync', 'PositionSynchronizer'),
            ('execution.circuit_breakers', 'CircuitBreakers'),
            ('config.prop_firm_configs', 'PROP_FIRM_CONFIGS'),
            ('config.bot_risk_params', 'BOT_RISK_PARAMS'),
            ('config.instrument_specs', 'INSTRUMENT_SPECS'),
            ('database.db_manager', 'DBManager'),
            ('data.tradovate_data_provider', 'TradovateDataProvider'),
        ]
        
        for module_name, class_name in modules_to_test:
            try:
                module = __import__(module_name, fromlist=[class_name])
                getattr(module, class_name)
                print_success(f"{module_name}.{class_name}")
                self.checks_passed += 1
            except Exception as e:
                print_error(f"{module_name}.{class_name}: {str(e)}")
                self.checks_failed += 1
                
    def check_database(self):
        """Check database connectivity and schema."""
        print_header("3. DATABASE")
        
        try:
            from database.db_manager import DBManager
            
            db_path = os.getenv('DB_PATH', './database/trading_analysis.db')
            
            # Check if database file exists
            if Path(db_path).exists():
                print_success(f"Database file exists: {db_path}")
                self.checks_passed += 1
            else:
                print_warning(f"Database file not found: {db_path}")
                print_info("Run: python scripts/setup_demo_account.py")
                self.checks_warnings += 1
                
            # Try to connect
            try:
                db = DBManager(db_path)
                print_success("Database connection successful")
                self.checks_passed += 1
                
                # Check if tables exist
                conn = db.get_connection()
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                
                required_tables = [
                    'raw_video_trades',
                    'backtest_results',
                    'strategy_params',
                    'live_trades',
                    'daily_account_summary'
                ]
                
                for table in required_tables:
                    if table in tables:
                        print_success(f"Table exists: {table}")
                        self.checks_passed += 1
                    else:
                        print_error(f"Table missing: {table}")
                        self.checks_failed += 1
                        
            except Exception as e:
                print_error(f"Database connection failed: {e}")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"Database module error: {e}")
            self.checks_failed += 1
            
    def check_configuration(self):
        """Check configuration files."""
        print_header("4. CONFIGURATION")
        
        try:
            from config.prop_firm_configs import PROP_FIRM_CONFIGS
            from config.bot_risk_params import BOT_RISK_PARAMS
            from config.instrument_specs import INSTRUMENT_SPECS
            
            # Check Topstep 50K config
            if 'TOPSTEP_50K' in PROP_FIRM_CONFIGS:
                config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
                print_success(f"Topstep 50K config loaded")
                print_info(f"  Account Size: ${config.get('account_size', 'N/A'):,}")
                print_info(f"  Profit Target: ${config.get('profit_target', 'N/A'):,}")
                print_info(f"  Max Loss Limit: ${config.get('max_loss_limit', 'N/A'):,}")
                print_info(f"  Max Contracts: {config.get('max_contracts', 'N/A')}")
                self.checks_passed += 1
            else:
                print_error("TOPSTEP_50K config not found")
                self.checks_failed += 1
            
            # Check MES instrument specs
            if 'MES' in INSTRUMENT_SPECS:
                specs = INSTRUMENT_SPECS['MES']
                print_success(f"MES instrument specs loaded")
                print_info(f"  Point Value: ${specs.get('point_value', 'N/A')}")
                print_info(f"  Tick Size: {specs.get('tick_size', 'N/A')}")
                self.checks_passed += 1
            else:
                print_error("MES instrument specs not found")
                self.checks_failed += 1
            
            # Check risk params
            required_params = [
                'max_daily_trades',
                'max_consecutive_losses',
                'max_risk_per_trade_pct',
            ]
            
            for param in required_params:
                if param in BOT_RISK_PARAMS:
                    print_success(f"Risk param: {param} = {BOT_RISK_PARAMS[param]}")
                    self.checks_passed += 1
                else:
                    print_error(f"Missing risk param: {param}")
                    self.checks_failed += 1
                    
        except Exception as e:
            print_error(f"Configuration error: {e}")
            self.checks_failed += 1
            
    def check_risk_manager(self):
        """Check RiskManager functionality."""
        print_header("5. RISK MANAGER")
        
        try:
            from core.risk_manager import RiskManager
            from config.prop_firm_configs import PROP_FIRM_CONFIGS
            
            config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
            risk_manager = RiskManager(config)
            
            print_success("RiskManager initialized")
            self.checks_passed += 1
            
            # Test daily trade limit
            risk_manager.daily_trade_count = 5
            market_state = {"timestamp": datetime.now(), "hour": 10}
            account_state = {"equity": 50000, "mll_floor": 48000}
            
            allowed, reason = risk_manager.can_trade(market_state, account_state)
            if not allowed and "DAILY_TRADE_LIMIT" in reason:
                print_success("Daily trade limit enforcement works")
                self.checks_passed += 1
            else:
                print_error("Daily trade limit not working")
                self.checks_failed += 1
            
            # Reset and test consecutive losses
            risk_manager.reset_daily_counters(datetime.now().date())
            risk_manager.consecutive_losses = 3
            
            allowed, reason = risk_manager.can_trade(market_state, account_state)
            if not allowed and "consecutive" in reason.lower():
                print_success("Consecutive losses enforcement works")
                self.checks_passed += 1
            else:
                print_error("Consecutive losses check not working")
                self.checks_failed += 1
            
            # Test position size calculation
            contracts = risk_manager.calculate_position_size(
                account_equity=50000,
                atr=7.5,
                stop_distance=5.0,
                point_value=5,
                confidence=0.7
            )
            
            if 1 <= contracts <= 5:
                print_success(f"Position size calculation: {contracts} contracts")
                self.checks_passed += 1
            else:
                print_error(f"Invalid position size: {contracts}")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"RiskManager error: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()
            self.checks_failed += 1
            
    def check_position_sizer(self):
        """Check PositionSizer functionality."""
        print_header("6. POSITION SIZER")
        
        try:
            from core.position_sizer import PositionSizer
            from core.risk_manager import RiskManager
            from config.prop_firm_configs import PROP_FIRM_CONFIGS
            from config.instrument_specs import INSTRUMENT_SPECS
            
            config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
            risk_manager = RiskManager(config)
            instrument = INSTRUMENT_SPECS['MES']
            
            sizer = PositionSizer(risk_manager, instrument)
            print_success("PositionSizer initialized")
            self.checks_passed += 1
            
            # Test size calculation
            result = sizer.calculate_size(
                account_equity=50000,
                entry_price=5000.0,
                stop_price=4995.0,
                atr=7.5,
                signal_confidence=0.7,
                market_state="BALANCED"
            )
            
            if result['contracts'] >= 1:
                print_success(f"Position size: {result['contracts']} contracts")
                print_info(f"  Risk: ${result['risk_dollars']:.2f} ({result['risk_pct']:.2f}%)")
                self.checks_passed += 1
            else:
                print_error("Position size calculation failed")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"PositionSizer error: {e}")
            self.checks_failed += 1
            
    def check_signal_generator(self):
        """Check SignalGenerator functionality."""
        print_header("7. SIGNAL GENERATOR")
        
        try:
            from core.signal_generator import SignalGenerator
            
            # Test with fallback mode (0 video trades)
            gen = SignalGenerator(video_trade_count=0, use_fallback=True)
            print_success("SignalGenerator initialized (fallback mode)")
            self.checks_passed += 1
            
            # Test signal generation
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
            
            if signal['action'] == 'BUY':
                print_success(f"Signal generated: {signal['action']} {signal['setup_type']}")
                print_info(f"  Confidence: {signal['confidence']}")
                print_info(f"  Mode: {gen.get_signal_mode()}")
                self.checks_passed += 1
            else:
                print_error("Signal generation failed")
                self.checks_failed += 1
            
            # Test with video data mode
            gen_with_data = SignalGenerator(video_trade_count=50, use_fallback=True)
            if gen_with_data.get_signal_mode() == "VIDEO_DERIVED":
                print_success("Video-derived mode working")
                self.checks_passed += 1
            else:
                print_error("Video-derived mode not working")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"SignalGenerator error: {e}")
            self.checks_failed += 1
            
    def check_circuit_breakers(self):
        """Check CircuitBreakers functionality."""
        print_header("8. CIRCUIT BREAKERS")
        
        try:
            from execution.circuit_breakers import CircuitBreakers
            from config.prop_firm_configs import PROP_FIRM_CONFIGS
            
            config = PROP_FIRM_CONFIGS['TOPSTEP_50K']
            circuit_breakers = CircuitBreakers(config)
            
            print_success(f"CircuitBreakers initialized ({len(circuit_breakers.breakers)} breakers)")
            self.checks_passed += 1
            
            # Test daily loss breaker
            context = {
                "daily_pnl": -900,  # Exceeds 40% of MLL
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
                print_success("Daily loss circuit breaker works")
                self.checks_passed += 1
            else:
                print_error("Daily loss circuit breaker not working")
                self.checks_failed += 1
            
            # Test with good context
            context_good = {
                "daily_pnl": 100,
                "equity": 51000,
                "mll_floor": 48000,
                "consecutive_losses": 0,
                "last_data_timestamp": datetime.now(),
                "last_broker_ping": datetime.now(),
                "recent_orders": [],
                "recent_fills": [],
                "recent_errors": [],
            }
            
            allowed, reason = circuit_breakers.check_all(context_good)
            if allowed:
                print_success("Circuit breakers allow trading when conditions good")
                self.checks_passed += 1
            else:
                print_error(f"Circuit breakers blocked unexpectedly: {reason}")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"CircuitBreakers error: {e}")
            self.checks_failed += 1
            
    def check_position_sync(self):
        """Check PositionSynchronizer functionality."""
        print_header("9. POSITION SYNCHRONIZATION")
        
        try:
            from execution.position_sync import PositionSynchronizer, Position
            from database.db_manager import DBManager
            
            # Create mock objects
            class MockProvider:
                base_url = "https://demo.tradovateapi.com/v1"
                def _headers(self):
                    return {"Authorization": "Bearer test"}
            
            db_path = os.getenv('DB_PATH', './database/trading_analysis.db')
            db = DBManager(db_path)
            provider = MockProvider()
            
            sync = PositionSynchronizer(provider, db)
            print_success("PositionSynchronizer initialized")
            self.checks_passed += 1
            
            # Test position status
            status = sync.get_position_status()
            if "broker_position" in status and "local_position" in status:
                print_success("Position status retrieval works")
                self.checks_passed += 1
            else:
                print_error("Position status incomplete")
                self.checks_failed += 1
            
            # Test Position dataclass
            pos = Position(
                instrument="MES",
                quantity=2,
                avg_price=5000.0,
                unrealized_pnl=100.0,
                realized_pnl=0.0
            )
            
            if pos.is_long and not pos.is_flat:
                print_success("Position dataclass works correctly")
                self.checks_passed += 1
            else:
                print_error("Position dataclass error")
                self.checks_failed += 1
                
        except Exception as e:
            print_error(f"PositionSync error: {e}")
            self.checks_failed += 1
            
    def check_webhook_server(self):
        """Check webhook server can be imported."""
        print_header("10. WEBHOOK SERVER")
        
        try:
            # Try to import the webhook server
            from execution.webhook_server_enhanced import app
            
            print_success("Webhook server module imports correctly")
            self.checks_passed += 1
            
            # Check if FastAPI app is properly configured
            if hasattr(app, 'routes') and len(app.routes) > 0:
                print_success(f"FastAPI app configured with {len(app.routes)} routes")
                self.checks_passed += 1
            else:
                print_error("FastAPI app has no routes")
                self.checks_failed += 1
            
            # List available endpoints
            print_info("Available endpoints:")
            for route in app.routes:
                if hasattr(route, 'methods') and hasattr(route, 'path'):
                    methods = ','.join(route.methods)
                    print_info(f"  {methods} {route.path}")
                    
        except Exception as e:
            print_error(f"Webhook server error: {e}")
            self.checks_failed += 1
            
    def print_summary(self):
        """Print validation summary."""
        print_header("VALIDATION SUMMARY")
        
        total = self.checks_passed + self.checks_failed + self.checks_warnings
        
        print(f"Checks Passed:   {Colors.GREEN}{self.checks_passed}{Colors.END}")
        print(f"Checks Failed:   {Colors.RED}{self.checks_failed}{Colors.END}")
        print(f"Warnings:        {Colors.YELLOW}{self.checks_warnings}{Colors.END}")
        print(f"Total Checks:    {total}")
        print()
        
        if self.checks_failed == 0:
            print(f"{Colors.GREEN}{Colors.BOLD}✓ ALL CHECKS PASSED!{Colors.END}")
            print()
            print("Your paper trading system is ready to use!")
            print()
            print("Next steps:")
            print("  1. Start the webhook server:")
            print("     uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000")
            print()
            print("  2. Test with curl commands (see PAPER_TRADING_QUICK_START.md)")
            print()
            print("  3. Configure TradingView webhooks")
            print()
            sys.exit(0)
        else:
            print(f"{Colors.RED}{Colors.BOLD}✗ VALIDATION FAILED{Colors.END}")
            print()
            print("Please fix the errors above before starting paper trading.")
            print()
            sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Validate paper trading system setup'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    validator = PaperTradingValidator(verbose=args.verbose)
    validator.run_all_checks()


if __name__ == "__main__":
    main()
