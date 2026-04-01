#!/usr/bin/env python3
"""
Test Runner for Paper Trading System
====================================

Runs all paper trading tests and generates a report.

Usage:
    python scripts/run_tests.py
    python scripts/run_tests.py --verbose
    python scripts/run_tests.py --test integration
"""

import subprocess
import sys
import argparse
from datetime import datetime


def run_command(cmd, description):
    """Run a shell command and print output."""
    print(f"\n{'='*70}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print('='*70 + "\n")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        
        print(result.stdout)
        
        if result.stderr:
            print("STDERR:", result.stderr)
        
        return result.returncode == 0
        
    except FileNotFoundError as e:
        print(f"ERROR: Command not found - {e}")
        print("\nMake sure you have pytest installed:")
        print("  pip install pytest pytest-asyncio")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Run paper trading tests')
    parser.add_argument(
        '--test', '-t',
        choices=['unit', 'integration', 'all'],
        default='all',
        help='Which tests to run (default: all)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    parser.add_argument(
        '--failfast', '-f',
        action='store_true',
        help='Stop on first failure'
    )
    
    args = parser.parse_args()
    
    print("="*70)
    print("VWAP BOT - TEST RUNNER")
    print("="*70)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Build pytest command
    pytest_cmd = ['python', '-m', 'pytest']
    
    if args.verbose:
        pytest_cmd.append('-v')
    
    if args.failfast:
        pytest_cmd.append('-x')
    
    # Color output
    pytest_cmd.append('--color=yes')
    
    results = []
    
    # Run unit tests
    if args.test in ['unit', 'all']:
        print("\n" + "="*70)
        print("UNIT TESTS")
        print("="*70)
        
        unit_tests = [
            ('tests/test_risk_manager.py', 'Risk Manager Tests'),
            ('tests/test_position_sizer.py', 'Position Sizer Tests'),
            ('tests/test_signal_generator.py', 'Signal Generator Tests'),
            ('tests/test_signal_generator_enhanced.py', 'Signal Generator Enhanced Tests'),
            ('tests/test_prop_firm_simulator.py', 'Prop Firm Simulator Tests'),
            ('tests/test_vwap_calculator.py', 'VWAP Calculator Tests'),
            ('tests/test_position_sync.py', 'Position Sync Tests'),
            ('tests/test_circuit_breakers.py', 'Circuit Breakers Tests'),
        ]
        
        for test_file, description in unit_tests:
            cmd = pytest_cmd + [test_file, '-v']
            success = run_command(cmd, description)
            results.append((description, success))
    
    # Run integration tests
    if args.test in ['integration', 'all']:
        print("\n" + "="*70)
        print("INTEGRATION TESTS")
        print("="*70)
        
        cmd = pytest_cmd + ['tests/integration/test_paper_trading.py', '-v']
        success = run_command(cmd, 'Paper Trading Integration Tests')
        results.append(('Integration Tests', success))
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, success in results if success)
    failed = sum(1 for _, success in results if not success)
    total = len(results)
    
    for description, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"{status}: {description}")
    
    print()
    print(f"Results: {passed}/{total} passed")
    
    if failed == 0:
        print("\n✓ ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n✗ {failed} test suite(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
