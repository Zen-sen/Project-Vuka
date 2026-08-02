import sys
sys.path.insert(0, '.')

print("Testing imports...")

# Test state_manager_v4_6
try:
    import state_manager_v4_6
    print("✓ state_manager_v4_6 imported")
except ImportError as e:
    print(f"✗ state_manager_v4_6 import failed: {e}")

# Test health_monitor_v4_6
try:
    import health_monitor_v4_6
    print("✓ health_monitor_v4_6 imported")
except ImportError as e:
    print(f"✗ health_monitor_v4_6 import failed: {e}")

# Test kronos_guardian_v4_6
try:
    import kronos_guardian_v4_6
    print("✓ kronos_guardian_v4_6 imported")
except ImportError as e:
    print(f"✗ kronos_guardian_v4_6 import failed: {e}")
