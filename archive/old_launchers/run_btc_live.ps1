# BTCUSD INGWE + Kronos AI Launcher
# Run this in PowerShell

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   BTCUSD INGWE + KRONOS AI - LAUNCHER" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check if Python is available
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "ERROR: Python not found. Please install Python first." -ForegroundColor Red
    exit 1
}

Write-Host "Step 1: Starting Kronos AI Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'C:\Users\classic\Desktop\Project Vuka'; python kronos_server.py" -WindowStyle Normal -PassThru

Write-Host "Waiting 10 seconds for Kronos to load..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Check if Kronos is running
try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
    if ($response.status -eq "ok") {
        Write-Host "Kronos AI is ready!" -ForegroundColor Green
    }
} catch {
    Write-Host "Warning: Kronos may not be ready yet. Continue anyway." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Step 2: Starting BTCUSD INGWE Bot..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd 'C:\Users\classic\Desktop\Project Vuka'; python ingwe.py BTCUSD INGWE" -WindowStyle Normal -PassThru

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   BOTH TERMINALS SHOULD NOW BE OPEN" -ForegroundColor Green
Write-Host ""
Write-Host "To check trades, run: type trades_BTCUSD_INGWE.json" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan

# Keep script running briefly
Start-Sleep -Seconds 3