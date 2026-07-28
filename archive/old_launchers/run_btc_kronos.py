#!/usr/bin/env python3
"""Run BTCUSD with Kronos - Use CMD or PowerShell to run"""

import subprocess
import sys
import time
import os

os.chdir(r"C:\Users\classic\Desktop\Project Vuka")

print("=" * 60)
print("BTCUSD INGWE + KRONOS AI")
print("=" * 60)
print()
print("To run in terminals, use these commands:")
print()
print("TERMINAL 1 - Kronos AI:")
print("  cd C:\\Users\\classic\\Desktop\\Project Vuka")
print("  python kronos_server.py")
print()
print("TERMINAL 2 - BTCUSD Bot:")
print("  cd C:\\Users\\classic\\Desktop\\Project Vuka")
print("  python ingwe.py BTCUSD INGWE")
print()
print("=" * 60)
print("Starting quick validation...")

# Quick check
result = subprocess.run(
    [sys.executable, "ingwe.py", "BTCUSD", "INGWE", "--check"],
    capture_output=True,
    text=True,
    timeout=30
)

print(result.stdout)
if result.stderr:
    print("Errors:", result.stderr[:500])

print()
print("Validation complete. Open terminals above to run live.")