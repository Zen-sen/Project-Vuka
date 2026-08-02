import json
from pathlib import Path

for s in ["EURUSD", "GBPUSD"]:
    f = Path("trades_%s_INGWE.json" % s)
    if f.exists():
        data = json.load(open(f))
        t = data if isinstance(data, list) else data.get("trades", [])
        print("%s: %d trades" % (s, len(t)))
    else:
        print("%s: no file" % s)