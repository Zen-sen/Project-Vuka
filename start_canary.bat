@echo off
REM Phase 2a: Canary Deployment Script
REM Starts event-driven tick engine on EURUSD_INGWE instance
REM
REM Prerequisites:
REM   - MT5 terminal running with active connection
REM   - Kronos server running (or will be started separately)
REM   - vuka_trading.db present and initialized

setlocal enabledelayedexpansion

cd /D "%~dp0"

echo.
echo ================================================================================
echo PHASE 2A: CANARY DEPLOYMENT - EVENT-DRIVEN TICK ENGINE
echo ================================================================================
echo.
echo Starting EURUSD_INGWE in event-driven mode (tick-stream execution)
echo Monitoring period: 24 hours
echo.

REM Check if MT5 is accessible
echo [1/3] Verifying configuration...
python3.14.exe -m vuka.core.bot EURUSD INGWE --check > nul 2>&1
if errorlevel 1 (
    echo ✗ Configuration check failed. Verify MT5 is running and vuka_trading.db exists.
    exit /b 1
)
echo ✓ Configuration valid

REM Check if tick engine is available
echo [2/3] Verifying tick engine...
python3.14.exe -c "from tick_engine_v5 import TickEngine; print('OK')" > nul 2>&1
if errorlevel 1 (
    echo ✗ Tick engine not found (tick_engine_v5.py missing or has errors)
    exit /b 1
)
echo ✓ Tick engine available

REM Start canary
echo [3/3] Starting canary deployment...
echo.
echo ================================================================================
echo CANARY: EURUSD_INGWE (Event-Driven Mode)
echo Started: %date% %time%
echo ================================================================================
echo.

REM Create output log file with timestamp
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set logfile=logs\canary_%mydate%_%mytime%.log

python3.14.exe -m vuka.core.bot EURUSD INGWE --live 2>&1 | tee "%logfile%"

echo.
echo ================================================================================
echo Canary deployment ended at %date% %time%
echo Log saved to: %logfile%
echo ================================================================================
