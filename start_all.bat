@echo off
title Project Vuka
cd /d "C:\Users\classic\Desktop\Project Vuka"

echo ==========================================
echo    PROJECT VUKA — ONE-CLICK LAUNCH
echo ==========================================
echo.

REM Kill any stale processes first
call vuka.bat stop > nul 2>&1

REM Wait for MT5 / network
timeout /t 5 /nobreak > nul

REM Start Kronos AI server (must be ready before bots)
echo [1/3] Starting Kronos AI Server...
start "Kronos Server" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" kronos_server.py
timeout /t 8 /nobreak > nul

REM Start supervisor (launches all 4 bot instances)
echo [2/3] Starting Supervisor (manages all bots)...
start "Vuka Supervisor" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" supervisor.py
timeout /t 3 /nobreak > nul

REM Launch the dashboard
echo [3/3] Opening Dashboard...
start "Vuka Dashboard" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" dashboard.py

echo.
echo ==========================================
echo  ALL SYSTEMS STARTED
echo ==========================================
echo.
echo  Supervisor  : Manages 4 bots (EURUSD/GBPUSD x INGWE/SB)
echo  Kronos      : AI trade validation server
echo  Dashboard   : Live fleet monitor
echo.
echo  Commands:
echo    vuka status    —  Check what's running
echo    vuka stop      —  Stop everything
echo    vuka dashboard —  Open dashboard only
echo.
pause
