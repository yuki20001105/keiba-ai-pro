"""Offline accuracy-first feature search and separate betting-rule audit."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python-api'),str(ROOT/'keiba')]

if __name__=='__main__':
    from experiments.search import main
    raise SystemExit(main())
