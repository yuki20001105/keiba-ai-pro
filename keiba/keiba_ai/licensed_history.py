"""Validated, append-only ingestion for licensed historical race exports."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .point_in_time_odds import PointInTimeOddsPolicy, select_point_in_time_win_odds

MANIFEST_SCHEMA = "licensed-keiba-history-manifest-v1"
RECORD_SCHEMA = "licensed-keiba-history-runner-v1"
RACE_ID_PATTERN = re.compile(r"^\d{12}$")
ALLOWED_FILE_FORMATS = frozenset({"jsonl"})
REQUIRED_PAYLOAD_FIELDS = frozenset(
    {"distance", "surface", "finish", "time_seconds", "horse_number"}
)


@dataclass(frozen=True)
class PreparedHistoryImport:
    manifest: dict[str, Any]
    manifest_sha256: str
    records: tuple[dict[str, Any], ...]
    coverage_start: str
    coverage_end: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: Any, field: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{field} must be a valid timezone-aware timestamp")
    return pd.Timestamp(parsed)


def _validated_relative_file(manifest_path: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError("manifest file paths must be non-empty and relative")
    root = manifest_path.parent.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("manifest file path escapes the manifest directory") from exc
    if not resolved.is_file():
        raise ValueError(f"manifest data file does not exist: {relative}")
    return resolved


def _validate_runner_record(
    raw: Any,
    *,
    provider: str,
    allowed_years: frozenset[int],
    policy: PointInTimeOddsPolicy,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != RECORD_SCHEMA:
        raise ValueError(f"every record must use schema {RECORD_SCHEMA}")
    required = {
        "source_record_id",
        "race_id",
        "horse_id",
        "race_date",
        "post_time",
        "payload",
        "odds_snapshots",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError("runner record is missing: " + ", ".join(sorted(missing)))
    race_id = str(raw["race_id"]).strip()
    horse_id = str(raw["horse_id"]).strip()
    source_record_id = str(raw["source_record_id"]).strip()
    if not RACE_ID_PATTERN.fullmatch(race_id):
        raise ValueError(f"race_id must contain exactly 12 digits: {race_id!r}")
    if not horse_id or not source_record_id:
        raise ValueError("horse_id and source_record_id must be non-empty")
    race_date = pd.to_datetime(raw["race_date"], errors="coerce")
    if pd.isna(race_date) or int(race_date.year) not in allowed_years:
        raise ValueError(
            f"race_date is outside the approved import years: {raw['race_date']!r}"
        )
    if race_id[:8] != pd.Timestamp(race_date).strftime("%Y%m%d"):
        raise ValueError(f"race_id date and race_date disagree: {race_id}")
    post_time = _parse_utc(raw["post_time"], "post_time")

    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    missing_payload = REQUIRED_PAYLOAD_FIELDS - set(payload)
    if missing_payload:
        raise ValueError("payload is missing: " + ", ".join(sorted(missing_payload)))
    finish = pd.to_numeric(payload["finish"], errors="coerce")
    distance = pd.to_numeric(payload["distance"], errors="coerce")
    time_seconds = pd.to_numeric(payload["time_seconds"], errors="coerce")
    horse_number = pd.to_numeric(payload["horse_number"], errors="coerce")
    if any(pd.isna(value) for value in (finish, distance, time_seconds, horse_number)):
        raise ValueError(
            "finish, distance, time_seconds and horse_number must be numeric"
        )
    if finish < 1 or distance <= 0 or time_seconds <= 0 or horse_number < 1:
        raise ValueError("payload contains an out-of-range required value")

    odds_snapshots = raw["odds_snapshots"]
    if not isinstance(odds_snapshots, list) or not odds_snapshots:
        raise ValueError("odds_snapshots must be a non-empty list")
    snapshot_rows = []
    for snapshot in odds_snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("each odds snapshot must be an object")
        snapshot_rows.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "odds": snapshot.get("odds"),
                "observed_at": snapshot.get("observed_at"),
                "source": snapshot.get("source", provider),
                "snapshot_kind": snapshot.get("snapshot_kind"),
            }
        )
    selected = select_point_in_time_win_odds(
        pd.DataFrame(snapshot_rows),
        pd.DataFrame(
            [{"race_id": race_id, "post_time": post_time, "expected_runner_count": 1}]
        ),
        policy=policy,
    )
    if len(selected) != 1:
        raise ValueError(
            f"record {source_record_id!r} has no fresh pre-decision win-odds snapshot"
        )
    odds = selected.iloc[0]
    canonical_payload = dict(payload)
    canonical_payload.update(
        {
            "race_id": race_id,
            "horse_id": horse_id,
            "race_date": pd.Timestamp(race_date).strftime("%Y%m%d"),
            "date": pd.Timestamp(race_date).strftime("%Y%m%d"),
            "post_time": post_time.isoformat(),
            "odds": float(odds["odds"]),
            "odds_observed_at": pd.Timestamp(odds["odds_observed_at"]).isoformat(),
            "odds_cutoff_at": pd.Timestamp(odds["odds_cutoff_at"]).isoformat(),
            "odds_source": str(odds["odds_source"]),
            "odds_snapshot_kind": str(odds["odds_snapshot_kind"]),
            "licensed_source_provider": provider,
            "licensed_source_record_id": source_record_id,
        }
    )
    return {
        "source_record_id": source_record_id,
        "race_id": race_id,
        "horse_id": horse_id,
        "race_date": pd.Timestamp(race_date).date().isoformat(),
        "post_time": post_time.isoformat(),
        "payload": canonical_payload,
        "payload_sha256": _canonical_sha256(canonical_payload),
        "odds_snapshots": snapshot_rows,
    }


def prepare_history_import(
    manifest_path: Path,
    *,
    allowed_years: Iterable[int] = range(2019, 2025),
    odds_policy: PointInTimeOddsPolicy | None = None,
) -> PreparedHistoryImport:
    """Validate all bytes and records before any database write is attempted."""

    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    provider = str(manifest.get("provider", "")).strip()
    license_reference = str(manifest.get("license_reference", "")).strip()
    if not provider or not license_reference:
        raise ValueError("provider and license_reference are required")
    _parse_utc(manifest.get("exported_at"), "exported_at")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("manifest files must be a non-empty list")
    if manifest.get("complete_races") is not True:
        raise ValueError("manifest must attest complete_races=true")
    allowed_year_set = frozenset(int(year) for year in allowed_years)
    if not allowed_year_set:
        raise ValueError("allowed_years must not be empty")
    policy = odds_policy or PointInTimeOddsPolicy()

    records: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_runner_ids: set[tuple[str, str]] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "format"}:
            raise ValueError("each manifest file needs exactly path, sha256 and format")
        if item["format"] not in ALLOWED_FILE_FORMATS:
            raise ValueError(f"unsupported file format: {item['format']!r}")
        data_path = _validated_relative_file(manifest_path, str(item["path"]))
        expected_hash = str(item["sha256"]).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(
                "manifest sha256 must be 64 lowercase hexadecimal characters"
            )
        if sha256_file(data_path) != expected_hash:
            raise ValueError(f"sha256 mismatch for {item['path']}")
        with data_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    record = _validate_runner_record(
                        raw,
                        provider=provider,
                        allowed_years=allowed_year_set,
                        policy=policy,
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"{item['path']}:{line_number}: {exc}") from exc
                if record["source_record_id"] in seen_source_ids:
                    raise ValueError("duplicate source_record_id in import bundle")
                runner_key = (record["race_id"], record["horse_id"])
                if runner_key in seen_runner_ids:
                    raise ValueError("duplicate race_id/horse_id in import bundle")
                seen_source_ids.add(record["source_record_id"])
                seen_runner_ids.add(runner_key)
                records.append(record)
    if not records:
        raise ValueError("import bundle contains no records")
    by_race: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_race.setdefault(record["race_id"], []).append(record)
    for race_id, race_records in by_race.items():
        if len(race_records) < 5:
            raise ValueError(f"race {race_id} contains fewer than five runners")
        finishes = [int(record["payload"]["finish"]) for record in race_records]
        horse_numbers = [
            int(record["payload"]["horse_number"]) for record in race_records
        ]
        if finishes.count(1) != 1:
            raise ValueError(f"race {race_id} must contain exactly one winner")
        if len(set(horse_numbers)) != len(horse_numbers):
            raise ValueError(f"race {race_id} contains duplicate horse_number values")
        if len({record["race_date"] for record in race_records}) != 1:
            raise ValueError(f"race {race_id} contains inconsistent race dates")
        if len({record["post_time"] for record in race_records}) != 1:
            raise ValueError(f"race {race_id} contains inconsistent post times")
    dates = [record["race_date"] for record in records]
    manifest_hash = sha256_file(manifest_path)
    return PreparedHistoryImport(
        manifest=manifest,
        manifest_sha256=manifest_hash,
        records=tuple(records),
        coverage_start=min(dates),
        coverage_end=max(dates),
    )


def _ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS licensed_history_imports (
            import_id TEXT PRIMARY KEY,
            manifest_sha256 TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            license_reference TEXT NOT NULL,
            exported_at TEXT NOT NULL,
            coverage_start TEXT NOT NULL,
            coverage_end TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS licensed_history_entries (
            source_record_id TEXT PRIMARY KEY,
            import_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            race_id TEXT NOT NULL,
            horse_id TEXT NOT NULL,
            race_date TEXT NOT NULL,
            post_time TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            FOREIGN KEY(import_id) REFERENCES licensed_history_imports(import_id),
            UNIQUE(provider, race_id, horse_id)
        );
        CREATE INDEX IF NOT EXISTS idx_licensed_history_race
            ON licensed_history_entries(race_id);
        """)


def apply_history_import(
    database: Path,
    prepared: PreparedHistoryImport,
) -> dict[str, Any]:
    """Append a fully validated import in one transaction.

    Reapplying identical bytes is idempotent.  Any collision with different
    bytes aborts the complete transaction; existing history is never updated.
    """

    database = database.resolve()
    if not database.is_file():
        raise ValueError(f"database does not exist: {database}")
    imported_at = datetime.now(timezone.utc).isoformat()
    import_id = f"licensed-{prepared.manifest_sha256[:24]}"
    inserted = 0
    skipped = 0
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _ensure_tables(connection)
        existing_import = connection.execute(
            "SELECT import_id FROM licensed_history_imports WHERE manifest_sha256 = ?",
            (prepared.manifest_sha256,),
        ).fetchone()
        if existing_import and existing_import[0] != import_id:
            raise ValueError("manifest hash is already bound to a different import")
        connection.execute(
            """
            INSERT OR IGNORE INTO licensed_history_imports (
                import_id, manifest_sha256, provider, license_reference, exported_at,
                coverage_start, coverage_end, record_count, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                prepared.manifest_sha256,
                prepared.manifest["provider"],
                prepared.manifest["license_reference"],
                str(prepared.manifest["exported_at"]),
                prepared.coverage_start,
                prepared.coverage_end,
                len(prepared.records),
                imported_at,
            ),
        )
        for record in prepared.records:
            existing = connection.execute(
                """
                SELECT source_record_id, payload_sha256
                FROM licensed_history_entries
                WHERE source_record_id = ? OR (provider = ? AND race_id = ? AND horse_id = ?)
                """,
                (
                    record["source_record_id"],
                    prepared.manifest["provider"],
                    record["race_id"],
                    record["horse_id"],
                ),
            ).fetchone()
            if existing:
                if (
                    existing[0] != record["source_record_id"]
                    or existing[1] != record["payload_sha256"]
                ):
                    raise ValueError(
                        "append-only conflict for "
                        f"{record['race_id']}/{record['horse_id']}"
                    )
                skipped += 1
                continue
            connection.execute(
                """
                INSERT INTO licensed_history_entries (
                    source_record_id, import_id, provider, race_id, horse_id,
                    race_date, post_time, payload_json, payload_sha256, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["source_record_id"],
                    import_id,
                    prepared.manifest["provider"],
                    record["race_id"],
                    record["horse_id"],
                    record["race_date"],
                    record["post_time"],
                    json.dumps(
                        record["payload"],
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                    ),
                    record["payload_sha256"],
                    imported_at,
                ),
            )
            inserted += 1
    return {
        "schema": "licensed-keiba-history-import-report-v1",
        "database": str(database),
        "import_id": import_id,
        "manifest_sha256": prepared.manifest_sha256,
        "provider": prepared.manifest["provider"],
        "coverage_start": prepared.coverage_start,
        "coverage_end": prepared.coverage_end,
        "validated_record_count": len(prepared.records),
        "inserted_record_count": inserted,
        "idempotently_skipped_record_count": skipped,
        "append_only": True,
    }


def load_licensed_history_entries(
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Load normalized sidecar rows when the append-only table exists."""

    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='licensed_history_entries'"
    ).fetchone()
    if not exists:
        return []
    rows = connection.execute(
        "SELECT payload_json FROM licensed_history_entries ORDER BY race_date, race_id, horse_id"
    ).fetchall()
    return [json.loads(row[0]) for row in rows]
