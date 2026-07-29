@echo off
cd /d "C:\Users\classic\Desktop\Project Vuka"
set PYTHON=C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe

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
"%PYTHON%" -m vuka.core.launcher stop
echo.
goto end

:status
echo.
echo ====== VUKA STATUS ======
echo.
"%PYTHON%" -m vuka.core.status
echo.
goto end

:dashboard
start "Vuka Dashboard" /min "C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m vuka.core.dashboard
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