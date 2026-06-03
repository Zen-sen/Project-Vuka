@echo off
cd /d "C:\Users\classic\Desktop\Project Vuka"

if "%1"=="" goto help
if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="status" goto status
if "%1"=="dashboard" goto dashboard
if "%1"=="restart" goto restart
goto help

:start
REM Single entry point — delegates to start_all.bat
call start_all.bat
goto end

:restart
echo Restarting Project Vuka...
call vuka.bat stop
timeout /t 3 /nobreak > nul
call start_all.bat
goto end

:stop
echo Stopping all Vuka processes...
"%PYTHON%" launcher.py stop
echo.
goto end

:status
echo.
echo ====== VUKA STATUS ======
echo.
"C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" -c "
import psutil
procs = []
for proc in psutil.process_iter(['pid','name','cmdline']):
    try:
        c = ' '.join(proc.info.get('cmdline') or [])
        n = proc.info.get('name','')
        if n == 'python.exe' and any(x in c for x in ['ingwe.py','supervisor.py','dashboard.py','kronos_server.py']):
            parts = c.split()
            label = parts[1] if len(parts) > 1 else '?'
            args  = ' '.join(parts[2:4]) if len(parts) > 2 else ''
            procs.append((label, args, proc.info['pid']))
    except: pass
if not procs:
    print('  No Vuka processes running.')
else:
    for label, args, pid in sorted(procs):
        print(f'  PID {pid:>6}  {label:30s}  {args}')

expected = {'kronos_server.py','supervisor.py','dashboard.py'}
running  = {p[0].split('\\\\')[-1] for p in procs}
missing  = expected - running
if missing:
    print()
    for m in missing:
        print(f'  !! MISSING: {m}')
"
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
echo  vuka start       Start everything (Kronos, Supervisor, Dashboard)
echo  vuka stop        Kill all Vuka processes
echo  vuka restart     Stop then start everything fresh
echo  vuka status      Show running processes + missing components
echo  vuka dashboard   Open live monitor only
echo.
goto end

:end