@echo off
title Project Vuka — Auto-Start
cd /d "C:\Users\classic\Desktop\Project Vuka"

echo [%date% %time%] Starting Project Vuka...

REM Wait for network / MT5 terminal
timeout /t 10 /nobreak > nul

REM Start supervisor (launches all 4 bot instances)
start "Vuka Supervisor" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" supervisor.py

echo [%date% %time%] Supervisor started. Bots will launch within 30s.
echo Logs: logs\*.log
