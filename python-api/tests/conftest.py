from __future__ import annotations

import sys
from pathlib import Path


PYTHON_API_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_API_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_API_ROOT))

KEIBA_PACKAGE_ROOT = PYTHON_API_ROOT.parent / "keiba"
if str(KEIBA_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(KEIBA_PACKAGE_ROOT))
