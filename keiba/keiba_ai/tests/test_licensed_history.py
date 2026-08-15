from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from keiba_ai.licensed_history import apply_history_import, prepare_history_import
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame


def _write_bundle(
    root: Path, *, odds_observed_at: str = "2020-01-01T05:50:00Z"
) -> Path:
    records = []
    for horse_number in range(1, 6):
        records.append(
            {
                "schema": "licensed-keiba-history-runner-v1",
                "source_record_id": f"JV-202001010101-H{horse_number}",
                "race_id": "202001010101",
                "horse_id": f"H{horse_number}",
                "race_date": "2020-01-01",
                "post_time": "2020-01-01T06:00:00Z",
                "payload": {
                    "distance": 1600,
                    "surface": "turf",
                    "finish": horse_number,
                    "time_seconds": 94.2 + horse_number / 10,
                    "horse_number": horse_number,
                },
                "odds_snapshots": [
                    {
                        "odds": 2.5 + horse_number,
                        "observed_at": odds_observed_at,
                        "source": "JRA-VAN Data Lab",
                        "snapshot_kind": "pre_race",
                    }
                ],
            }
        )
    data = root / "runners.jsonl"
    data.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "licensed-keiba-history-manifest-v1",
                "provider": "JRA-VAN Data Lab",
                "license_reference": "local-subscriber-export",
                "exported_at": "2026-08-15T00:00:00Z",
                "complete_races": True,
                "files": [{"path": data.name, "sha256": digest, "format": "jsonl"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE race_results_ultimate (id INTEGER PRIMARY KEY, race_id TEXT, data TEXT)"
        )
        connection.execute(
            "CREATE TABLE races_ultimate (race_id TEXT PRIMARY KEY, data TEXT)"
        )
        connection.execute(
            "INSERT INTO race_results_ultimate (race_id, data) VALUES (?, ?)",
            (
                "202001010101",
                json.dumps({"race_id": "202001010101", "horse_id": "H1", "odds": 99.0}),
            ),
        )
    return path


def test_validated_import_is_append_only_and_idempotent(tmp_path: Path) -> None:
    prepared = prepare_history_import(_write_bundle(tmp_path))
    database = _database(tmp_path / "history.db")
    first = apply_history_import(database, prepared)
    second = apply_history_import(database, prepared)
    assert first["inserted_record_count"] == 5
    assert second["inserted_record_count"] == 0
    assert second["idempotently_skipped_record_count"] == 5
    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM licensed_history_entries WHERE horse_id = 'H1'"
            ).fetchone()[0]
        )
    assert payload["odds"] == 3.5
    assert payload["odds_snapshot_kind"] == "pre_race"
    assert payload["licensed_source_provider"] == "JRA-VAN Data Lab"
    loaded = load_ultimate_training_frame(database)
    assert len(loaded) == 5
    assert loaded["licensed_source_provider"].eq("JRA-VAN Data Lab").all()


def test_manifest_hash_mismatch_is_rejected_before_database_write(
    tmp_path: Path,
) -> None:
    manifest = _write_bundle(tmp_path)
    (tmp_path / "runners.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        prepare_history_import(manifest)


def test_result_time_odds_are_rejected(tmp_path: Path) -> None:
    manifest = _write_bundle(tmp_path, odds_observed_at="2020-01-01T06:01:00Z")
    with pytest.raises(ValueError, match="no fresh pre-decision"):
        prepare_history_import(manifest)


def test_out_of_scope_year_is_rejected(tmp_path: Path) -> None:
    manifest = _write_bundle(tmp_path)
    data = tmp_path / "runners.jsonl"
    content = (
        data.read_text(encoding="utf-8")
        .replace("2020-01-01", "2018-01-01")
        .replace("202001010101", "201801010101")
    )
    data.write_text(content, encoding="utf-8")
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["files"][0]["sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
    with pytest.raises(ValueError, match="approved import years"):
        prepare_history_import(manifest)
