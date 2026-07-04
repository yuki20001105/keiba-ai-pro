from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MLOPS_DB = ROOT / "keiba" / "data" / "mlops.db"
DEFAULT_RACE_DB = ROOT / "keiba" / "data" / "keiba_ultimate.db"


@dataclass
class FixtureInfo:
    sandbox_dir: str
    mlops_db_path: str
    race_db_path: str
    fixture_minimal: bool


def _sqlite_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"source db not found: {src}")
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()


def create_fixture(
    *,
    sandbox_db_path: str = "",
    fixture_minimal: bool = False,
    source_mlops_db: Path = DEFAULT_MLOPS_DB,
    source_race_db: Path = DEFAULT_RACE_DB,
) -> FixtureInfo:
    base_dir = ROOT / ".tmp" / "scenario_router_audit"
    base_dir.mkdir(parents=True, exist_ok=True)

    if sandbox_db_path:
        mlops_dst = Path(sandbox_db_path)
        sandbox_dir = mlops_dst.parent
    else:
        sandbox_dir = Path(tempfile.mkdtemp(prefix="sr_audit_", dir=str(base_dir)))
        mlops_dst = sandbox_dir / "mlops.sandbox.db"

    race_dst = sandbox_dir / "keiba_ultimate.sandbox.db"

    _sqlite_copy(source_mlops_db, mlops_dst)

    if fixture_minimal:
        # Minimal mode keeps race DB shared for faster setup.
        race_path = source_race_db
    else:
        _sqlite_copy(source_race_db, race_dst)
        race_path = race_dst

    return FixtureInfo(
        sandbox_dir=str(sandbox_dir),
        mlops_db_path=str(mlops_dst),
        race_db_path=str(race_path),
        fixture_minimal=bool(fixture_minimal),
    )


def cleanup_fixture(info: FixtureInfo) -> None:
    sandbox_dir = Path(info.sandbox_dir)
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Scenario Router audit sandbox fixture")
    parser.add_argument("--sandbox-db-path", default="", help="Optional fixed path for sandbox mlops DB")
    parser.add_argument("--fixture-minimal", action="store_true", help="Use minimal fixture (no race DB copy)")
    parser.add_argument("--output-json", default="", help="Write fixture metadata to JSON file")
    args = parser.parse_args()

    info = create_fixture(
        sandbox_db_path=str(args.sandbox_db_path or ""),
        fixture_minimal=bool(args.fixture_minimal),
    )
    payload = asdict(info)

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
