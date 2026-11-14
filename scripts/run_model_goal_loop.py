"""Offline, bounded KeibaAI goal-loop. Never activates models or places bets."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"repair-missingness", "verify-missingness"}:
        from scraping.missingness_goal import main
    else:
        from experiments.goal_loop import main
    raise SystemExit(main())
