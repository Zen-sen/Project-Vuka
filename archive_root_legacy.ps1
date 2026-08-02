# Archive root legacy files to archive/root-legacy/
New-Item -ItemType Directory -Force -Path "archive\root-legacy" | Out-Null

$legacy = @(
    "ingwe.py", "kronos_server.py", "kronos_server.py.bak2", "kronos_server.py.before_fix",
    "kronos_server.py.fixed", "kronos_server.py.orig", "kronos_server.py.patch_me",
    "kronos_server.py.work", "kronos_server.py.working", "supervisor.py", "dashboard.py",
    "state_manager_v4.6.py", "health_monitor_v4.6.py", "kronos_guardian_v4.6.py",
    "config_v4.6.json", "test_clean.py", "test_kronos.py", "test_mtf.py",
    "test_multi_pair.py", "test_ohlcv.py", "test_state.py", "test_v4.6.py"
)

foreach ($f in $legacy) {
    if (Test-Path $f) {
        Move-Item -Path $f -Destination "archive\root-legacy\" -Force
        Write-Host "Archived: $f"
    }
}

Get-ChildItem -Filter "*.csv" | ForEach-Object {
    Move-Item -Path $_.FullName -Destination "archive\root-legacy\" -Force
    Write-Host "Archived: $($_.Name)"
}

Write-Host "`nRoot cleaned. Only run.py and src/ remain active."
