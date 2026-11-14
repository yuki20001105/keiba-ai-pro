"""Research-only parallel target profile; leaves the original goal-loop intact."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

if __name__ == "__main__":
    from training.target_comparison import main
    raise SystemExit(main())
