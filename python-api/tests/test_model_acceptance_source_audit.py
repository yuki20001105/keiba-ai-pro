from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_model_acceptance_source.py"
MODEL_ID = "20260418_1928"

SPEC = importlib.util.spec_from_file_location("model_acceptance_source_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
source_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_audit)


def _database(tmp_path: Path, *, strict_columns: bool) -> Path:
    path = tmp_path / "source.db"
    strict = ""
    if strict_columns:
        strict = "," + ",".join(
            f"{column} REAL" if column.endswith("amount") or column == "latency_ms"
            else f"{column} TEXT"
            for column in source_audit.STRICT_SOURCE_COLUMNS
        )
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            f"""
            CREATE TABLE prediction_log (
                id INTEGER PRIMARY KEY,
                race_id TEXT NOT NULL,
                race_date TEXT NOT NULL,
                horse_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                predicted_at TEXT NOT NULL,
                win_probability REAL NOT NULL
                {strict}
            );
            CREATE TABLE race_results_ultimate (
                id INTEGER PRIMARY KEY,
                race_id TEXT NOT NULL,
                data TEXT NOT NULL
            );
            """
        )
        for index, finish in enumerate((1, 2), start=1):
            values: list[object] = [
                index,
                f"race-{index}",
                "20260402",
                f"horse-{index}",
                MODEL_ID,
                "2026-04-02T09:00:00+09:00",
                0.8 if finish == 1 else 0.2,
            ]
            columns = [
                "id",
                "race_id",
                "race_date",
                "horse_id",
                "model_id",
                "predicted_at",
                "win_probability",
            ]
            if strict_columns:
                columns.extend(source_audit.STRICT_SOURCE_COLUMNS)
                values.extend(
                    [
                        "2026-04-02T08:50:00+09:00",
                        "2026-04-02T10:00:00+09:00",
                        10.0,
                        20.0 if finish == 1 else 0.0,
                        10.0,
                        20.0 if finish == 1 else 0.0,
                        100.0,
                    ]
                )
            placeholders = ",".join("?" for _ in values)
            connection.execute(
                f"INSERT INTO prediction_log ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
            connection.execute(
                "INSERT INTO race_results_ultimate (id, race_id, data) VALUES (?, ?, ?)",
                (
                    index,
                    f"race-{index}",
                    json.dumps(
                        {"horse_id": f"horse-{index}", "finish_position": finish}
                    ),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def test_complete_source_is_ready_and_database_remains_unchanged(tmp_path: Path) -> None:
    database = _database(tmp_path, strict_columns=True)
    before = database.read_bytes()

    result = source_audit.audit_source(
        database.resolve(), model_id=MODEL_ID, training_cutoff=date(2026, 4, 1)
    )

    assert result["strict_observation_ready"] is True
    assert result["blockers"] == []
    assert result["counts"] == {
        "prediction_rows": 2,
        "same_day_prediction_rows": 2,
        "out_of_time_prediction_rows": 2,
        "settled_label_rows": 2,
        "win_rows": 1,
        "loss_rows": 1,
    }
    assert all(result["strict_source_capabilities"].values())
    assert database.read_bytes() == before
    assert not database.with_name(database.name + "-wal").exists()


def test_missing_strict_columns_are_reported_without_row_export(tmp_path: Path) -> None:
    database = _database(tmp_path, strict_columns=False)

    result = source_audit.audit_source(
        database.resolve(), model_id=MODEL_ID, training_cutoff=date(2026, 4, 1)
    )

    assert result["strict_observation_ready"] is False
    assert result["strict_source_capabilities"]["prediction_at_timezone"] is True
    assert all(
        result["strict_source_capabilities"][column] is False
        for column in source_audit.STRICT_SOURCE_COLUMNS
    )
    assert len(result["blockers"]) == len(source_audit.STRICT_SOURCE_COLUMNS)
    serialized = json.dumps(result)
    assert "horse-1" not in serialized
    assert str(database) not in serialized


def test_class_balance_and_out_of_time_fail_closed(tmp_path: Path) -> None:
    database = _database(tmp_path, strict_columns=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE prediction_log SET predicted_at = '2026-03-01T09:00:00+09:00'"
        )
        connection.commit()
    finally:
        connection.close()

    result = source_audit.audit_source(
        database.resolve(), model_id=MODEL_ID, training_cutoff=date(2026, 4, 1)
    )

    assert result["counts"]["out_of_time_prediction_rows"] == 0
    assert result["strict_observation_ready"] is False
    assert "out-of-time-predictions-absent" in result["blockers"]
    assert "settled-labels-insufficient" in result["blockers"]
    assert all(value is False for value in result["strict_source_capabilities"].values())


def test_naive_prediction_timestamp_is_not_strict_evidence(tmp_path: Path) -> None:
    database = _database(tmp_path, strict_columns=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE prediction_log SET predicted_at = '2026-04-02T09:00:00'"
        )
        connection.commit()
    finally:
        connection.close()

    result = source_audit.audit_source(
        database.resolve(), model_id=MODEL_ID, training_cutoff=date(2026, 4, 1)
    )

    assert result["strict_source_capabilities"]["prediction_at_timezone"] is False
    assert "source-prediction-at-timezone-incomplete" in result["blockers"]
    assert result["strict_observation_ready"] is False


@pytest.mark.parametrize("model_id", ["", "bad/model", "x" * 129])
def test_invalid_model_id_is_rejected(tmp_path: Path, model_id: str) -> None:
    database = _database(tmp_path, strict_columns=True)
    with pytest.raises(source_audit.SourceAuditError, match="model-id-invalid"):
        source_audit.audit_source(
            database.resolve(), model_id=model_id, training_cutoff=date(2026, 4, 1)
        )


def test_cli_emits_sanitized_nonready_result(tmp_path: Path) -> None:
    database = _database(tmp_path, strict_columns=False)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--database",
            str(database.resolve()),
            "--model-id",
            MODEL_ID,
            "--training-cutoff",
            "2026-04-01",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["strict_observation_ready"] is False
    assert completed.stderr == ""
