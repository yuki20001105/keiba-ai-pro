"""Entry point for the isolated, bounded pace research campaign."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

from experiments.pace_research_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
