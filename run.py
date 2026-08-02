#!/usr/bin/env python3
"""
run.py — Project Vuka v6.1 Entry Point
Thin wrapper so 'python run.py EURUSD INGWE' hits the real package.
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from vuka.core.bot import main

if __name__ == "__main__":
    main()
