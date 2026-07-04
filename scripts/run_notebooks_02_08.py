from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runner = root / "scripts" / "notebook_audit_runner.py"
    cmd = [
        sys.executable,
        str(runner),
        "--start",
        "2",
        "--end",
        "8",
        "--mode",
        "audit",
        "--cell-timeout",
        "600",
        "--notebook-timeout",
        "7200",
        "--max-retry",
        "3",
    ]
    return subprocess.call(cmd, cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(main())
