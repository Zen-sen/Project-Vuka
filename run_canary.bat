@echo off
REM Phase 2a: Canary Deployment Launch
REM Start EURUSD_INGWE in event-driven mode
REM This script launches the canary instance for 24-hour validation

cd /D "%~dp0"

echo.
echo ================================================================================
echo PHASE 2A: CANARY DEPLOYMENT - EVENT-DRIVEN TICK ENGINE
echo ================================================================================
echo.
echo Starting EURUSD_INGWE in event-driven mode (tick-stream execution)
echo Time: %date% %time%
echo.
echo Expected output:
echo   [TickEngine] Initialized: EURUSDc @ M15
echo   [TickEngine] Waiting for ticks...
echo   [TickEngine] Candle #1 @ ...
echo.

python3.14.exe -m vuka.core.bot EURUSD INGWE --live

echo.
echo ================================================================================
echo Canary ended at %date% %time%
echo Check logs/eurusd_ingwe.log for execution details
echo ================================================================================
