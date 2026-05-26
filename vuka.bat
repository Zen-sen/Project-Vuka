@echo off
cd /d "C:\Users\classic\Desktop\Project Vuka"

if "%1"=="" goto help
if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="status" goto status
if "%1"=="dashboard" goto dashboard
if "%1"=="restart" goto stop
goto help

:start
echo Starting Project Vuka...
start "Vuka Supervisor" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" supervisor.py
timeout /t 2 /nobreak > nul
start "Kronos Server" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" kronos_server.py
echo Supervisor + Kronos started.
echo Run: vuka dashboard  to open the monitor
goto end

:stop
echo Stopping all Vuka processes...
powershell -Command "Get-Process python | Where-Object { $_.CommandLine -match 'supervisor.py|ingwe.py|dashboard.py|kronos_server' } | ForEach-Object { $_.Kill(); Write-Host ('Killed ' + $_.Id) }" 2>nul
echo All processes stopped.
goto end

:status
echo.
echo ====== VUKA STATUS ======
echo.
"C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" -c "
import psutil
for proc in psutil.process_iter(['pid','name','cmdline']):
    try:
        c = ' '.join(proc.info.get('cmdline') or [])
        n = proc.info.get('name','')
        if n == 'python.exe' and ('ingwe.py' in c or 'supervisor.py' in c or 'dashboard.py' in c or 'kronos_server.py' in c):
            parts = c.split()
            label = parts[1] if len(parts) > 1 else '?'
            args = ' '.join(parts[2:5]) if len(parts) > 4 else ''
            print(f'  PID {proc.info[\"pid\"]:>6}  {label:25s}  {args}')
    except: pass
"
if errorlevel 1 echo  No processes running.
echo.
goto end

:dashboard
start "Vuka Dashboard" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" dashboard.py
echo Dashboard launched.
goto end

:help
echo.
echo ====== VUKA COMMAND CENTER ======
echo.
echo  vuka start       Start supervisor + Kronos server
echo  vuka stop        Kill all Vuka processes
echo  vuka restart     Stop then restart
echo  vuka status      Show running processes
echo  vuka dashboard   Open live monitor
echo  start_all.bat    One-click: start everything + dashboard
echo.
goto end

:end
