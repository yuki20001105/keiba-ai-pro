"""Publish the fixed approved pace74 artifact, never activate it.

Usage: python-api\\.venv\\Scripts\\python.exe scripts\\publish_pace74_shadow.py --publish
Read-only evidence counts: use --status. No request accepts artifact/source paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true", help="Create a new sealed reference-only package")
    mode.add_argument("--status", action="store_true", help="Read separate paired evidence counts; never promote")
    args = parser.parse_args()
    from services.pace74_shadow import evidence_status, publish_package
    result = publish_package() if args.publish else evidence_status()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
