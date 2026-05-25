@echo off
cd /d "C:\Users\classic\Desktop\Project Vuka"
echo ==========================================
echo  Project Vuka — Install Auto-Start
echo ==========================================
echo.
echo This will register a scheduled task that starts
echo the supervisor (and all bots) at every Windows boot.
echo.
echo IMPORTANT: Run this file AS ADMINISTRATOR.
echo (Right-click -> Run as administrator)
echo.
echo Task name: ProjectVuka
echo Script: start_all.bat
echo.
pause
echo.
schtasks /CREATE /SC ONSTART /TN "ProjectVuka" /TR "C:\Users\classic\Desktop\Project Vuka\start_all.bat" /RU %USERNAME% /IT /F
echo.
if %ERRORLEVEL% equ 0 (
    echo SUCCESS: Task registered.
    echo The bot will auto-start on next boot.
) else (
    echo FAILED: Try running this file as Administrator.
    echo Right-click ^"install_autostart.bat^" ^-^> ^"Run as administrator^"
)
echo.
pause
