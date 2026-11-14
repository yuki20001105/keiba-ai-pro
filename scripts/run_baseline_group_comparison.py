"""Run the isolated coarse speed-baseline comparison."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

from training.baseline_group_comparison import main


if __name__ == "__main__":
    raise SystemExit(main())
