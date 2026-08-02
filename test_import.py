import sys
sys.path.insert(0, '.')
try:
    import state_manager_v4_6
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
