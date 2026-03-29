
# Project Vuka - Ingwe.py UTC Expiry & Logging Fix

## Steps:

- [x] 1. Create TODO.md (done)
- [x] 2. Add `from datetime import datetime as _dt` to imports
- [x] 3. Replace `expiry_dt` assignment in `place_limit_order()`
- [x] 4. Replace log statement in `place_limit_order()`
- [x] 5. Verify changes (diffs confirmed exact matches, indentation preserved)
- [x] 6. Test and complete

✅ **All changes applied successfully.** Expiry now uses UTC timestamp (`int((datetime.now(timezone.utc) + timedelta(...)).timestamp())`) for MT5 `ORDER_TIME_SPECIFIED` compatibility. Logging updated to `{_dt.utcfromtimestamp(expiry_dt).strftime('%H:%M')} UTC`.
