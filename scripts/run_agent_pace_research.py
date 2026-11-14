"""Local entry point and owned worker for AI-directed pace research v3."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

from experiments.agent_pace_loop import main

if __name__ == "__main__":
    raise SystemExit(main())
