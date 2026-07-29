@echo off
title Project Vuka
cd /d "C:\Users\classic\Desktop\Project Vuka"
set PYTHON=C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe

echo ==========================================
echo    PROJECT VUKA — ONE-CLICK LAUNCH
echo ==========================================
echo.

REM Kill any stale processes first
call vuka.bat stop > nul 2>&1
timeout /t 5 /nobreak > nul

REM [1/3] Kronos FIRST — bots require it before scanning
echo [1/3] Starting Kronos AI Server...
start "Kronos Server" "%PYTHON%" -m vuka.ai.kronos_server
echo       Waiting 15s for Kronos to be ready (model download + load)...
timeout /t 15 /nobreak > nul

REM [2/3] Supervisor — launches 2 INGWE bot instances
echo [2/3] Starting Supervisor (manages all bots)...
start "Vuka Supervisor" /min "%PYTHON%" -m vuka.core.supervisor
timeout /t 3 /nobreak > nul

REM [3/3] Dashboard
echo [3/3] Opening Dashboard...
start "Vuka Dashboard" /min "%PYTHON%" -m vuka.core.dashboard

echo.
echo ==========================================
echo  ALL SYSTEMS STARTED
echo ==========================================
echo.
echo  Kronos           : Visible window — AI trade validation (check it's running)
echo  Supervisor       : Manages 2 bots (EURUSD/GBPUSD - INGWE only)
echo  Dashboard        : Live fleet monitor
echo.
echo  Commands:
echo    vuka status    —  Check what's running + flag missing components
echo    vuka stop      —  Stop everything
echo    vuka restart   —  Full restart
echo.
timeout /t 5 /nobreak > nul