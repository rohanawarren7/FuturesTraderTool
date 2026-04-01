@echo off
echo Starting VWAP Trading Bot - Paper Trading Mode
echo ============================================================
echo.
echo This will start the webhook server on port 8000
echo.
echo Once started, you can:
echo   - Test with: curl http://localhost:8000/health
echo   - Send trades to: http://localhost:8000/webhook/entry
echo.
echo Press Ctrl+C to stop
echo.
py -m uvicorn execution.webhook_server_enhanced:app --host 0.0.0.0 --port 8000
