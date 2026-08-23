from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


AUDIT_SCHEMA = "model-acceptance-source-audit"
SCHEMA_VERSION = 1
MAX_DATABASE_BYTES = 20 * 1024 * 1024 * 1024
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

BASE_PREDICTION_COLUMNS = frozenset(
    {
        "id",
        "race_id",
        "race_date",
        "horse_id",
        "model_id",
        "predicted_at",
        "win_probability",
    }
)
STRICT_SOURCE_COLUMNS = (
    "data_observed_at",
    "settled_at",
    "wager_amount",
    "return_amount",
    "baseline_wager_amount",
    "baseline_return_amount",
    "latency_ms",
)


def _timezone_timestamp(column: str) -> str:
    return (
        f"(substr({column}, -1) = 'Z' OR ("
        f"substr({column}, -6, 1) IN ('+', '-') AND substr({column}, -3, 1) = ':'))"
    )


class SourceAuditError(RuntimeError):
    """Sanitized read-only source audit failure."""


def _open_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_absolute() or database.is_symlink():
        raise SourceAuditError("source-audit-database-invalid")
    try:
        resolved = database.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise SourceAuditError("source-audit-database-unavailable") from exc
    if not resolved.is_file() or stat.st_size < 1 or stat.st_size > MAX_DATABASE_BYTES:
        raise SourceAuditError("source-audit-database-invalid")
    try:
        connection = sqlite3.connect(
            resolved.as_uri() + "?mode=ro&immutable=1",
            uri=True,
            timeout=5,
        )
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as exc:
        raise SourceAuditError("source-audit-database-unavailable") from exc


def _columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    try:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error as exc:
        raise SourceAuditError("source-audit-schema-invalid") from exc
    return frozenset(str(row[1]) for row in rows)


def _scalar_count(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
) -> int:
    try:
        value = connection.execute(sql, parameters).fetchone()
    except sqlite3.Error as exc:
        raise SourceAuditError("source-audit-query-failed") from exc
    if value is None or type(value[0]) is not int or value[0] < 0:
        raise SourceAuditError("source-audit-query-failed")
    return value[0]


def audit_source(
    database: Path,
    *,
    model_id: str,
    training_cutoff: date,
) -> dict[str, Any]:
    if MODEL_ID_RE.fullmatch(model_id or "") is None:
        raise SourceAuditError("source-audit-model-id-invalid")
    connection = _open_read_only(database)
    try:
        prediction_columns = _columns(connection, "prediction_log")
        result_columns = _columns(connection, "race_results_ultimate")
        if not BASE_PREDICTION_COLUMNS.issubset(prediction_columns) or not {
            "race_id",
            "data",
        }.issubset(result_columns):
            raise SourceAuditError("source-audit-schema-invalid")

        prediction_has_timezone = _timezone_timestamp("p.predicted_at")
        same_day = (
            "length(p.race_date) = 8 "
            "AND date(substr(p.race_date,1,4)||'-'||substr(p.race_date,5,2)||'-'||"
            "substr(p.race_date,7,2)) = CASE "
            f"WHEN {prediction_has_timezone} THEN date(p.predicted_at, '+9 hours') "
            "ELSE date(p.predicted_at) END"
        )
        after_cutoff = "date(p.predicted_at) > date(?)"
        parameters = (model_id, training_cutoff.isoformat())
        total = _scalar_count(
            connection,
            "SELECT COUNT(*) FROM prediction_log AS p WHERE p.model_id = ?",
            (model_id,),
        )
        same_day_count = _scalar_count(
            connection,
            f"SELECT COUNT(*) FROM prediction_log AS p WHERE p.model_id = ? AND {same_day}",
            (model_id,),
        )
        out_of_time_count = _scalar_count(
            connection,
            f"SELECT COUNT(*) FROM prediction_log AS p "
            f"WHERE p.model_id = ? AND {same_day} AND {after_cutoff}",
            parameters,
        )

        label_join = (
            "FROM prediction_log AS p "
            "JOIN race_results_ultimate AS r ON r.race_id = p.race_id "
            "AND CAST(json_extract(CASE WHEN json_valid(r.data) THEN r.data ELSE '{}' END, "
            "'$.horse_id') AS TEXT) = "
            "CAST(p.horse_id AS TEXT) "
            f"WHERE p.model_id = ? AND {same_day} AND {after_cutoff} "
            "AND json_valid(r.data) "
            "AND CAST(json_extract(CASE WHEN json_valid(r.data) THEN r.data ELSE '{}' END, "
            "'$.finish_position') AS INTEGER) > 0"
        )
        labeled_count = _scalar_count(
            connection,
            "SELECT COUNT(DISTINCT p.id) " + label_join,
            parameters,
        )
        win_count = _scalar_count(
            connection,
            "SELECT COUNT(DISTINCT CASE WHEN "
            "CAST(json_extract(CASE WHEN json_valid(r.data) THEN r.data ELSE '{}' END, "
            "'$.finish_position') AS INTEGER) = 1 "
            "THEN p.id END) "
            + label_join,
            parameters,
        )

        capabilities: dict[str, bool] = {}
        blockers: list[str] = []
        timezone_count = _scalar_count(
            connection,
            f"SELECT COUNT(*) FROM prediction_log AS p "
            f"WHERE p.model_id = ? AND {same_day} AND {after_cutoff} "
            f"AND {prediction_has_timezone}",
            parameters,
        )
        capabilities["prediction_at_timezone"] = (
            out_of_time_count > 0 and timezone_count == out_of_time_count
        )
        if not capabilities["prediction_at_timezone"]:
            blockers.append("source-prediction-at-timezone-incomplete")
        for column in STRICT_SOURCE_COLUMNS:
            available = column in prediction_columns
            complete_count = 0
            if available:
                completeness = f'p."{column}" IS NOT NULL'
                if column in {"data_observed_at", "settled_at"}:
                    completeness += f' AND {_timezone_timestamp(f"p.{column}")}'
                complete_count = _scalar_count(
                    connection,
                    f"SELECT COUNT(*) FROM prediction_log AS p "
                    f"WHERE p.model_id = ? AND {same_day} AND {after_cutoff} "
                    f"AND {completeness}",
                    parameters,
                )
            capabilities[column] = (
                available
                and out_of_time_count > 0
                and complete_count == out_of_time_count
            )
            if not capabilities[column]:
                blockers.append(f"source-{column.replace('_', '-')}-incomplete")

        if total == 0:
            blockers.append("model-predictions-absent")
        if same_day_count == 0:
            blockers.append("same-day-predictions-absent")
        if out_of_time_count == 0:
            blockers.append("out-of-time-predictions-absent")
        if labeled_count < 2:
            blockers.append("settled-labels-insufficient")
        if win_count == 0 or labeled_count - win_count == 0:
            blockers.append("settled-label-class-balance-missing")

        blockers = list(dict.fromkeys(blockers))
        return {
            "audit_schema": AUDIT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "model_id": model_id,
            "training_cutoff": training_cutoff.isoformat(),
            "counts": {
                "prediction_rows": total,
                "same_day_prediction_rows": same_day_count,
                "out_of_time_prediction_rows": out_of_time_count,
                "settled_label_rows": labeled_count,
                "win_rows": win_count,
                "loss_rows": max(0, labeled_count - win_count),
            },
            "strict_source_capabilities": capabilities,
            "blockers": blockers,
            "strict_observation_ready": not blockers,
        }
    finally:
        connection.close()


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("training cutoff must be YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit SQLite source completeness without exporting row data."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--training-cutoff", required=True, type=_iso_date)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        result = audit_source(
            args.database,
            model_id=args.model_id,
            training_cutoff=args.training_cutoff,
        )
    except (SourceAuditError, ValueError):
        print(json.dumps({"success": False, "code": "model-acceptance-source-audit-failed"}))
        return 1
    print(json.dumps(result, allow_nan=False, ensure_ascii=True, sort_keys=True))
    return 0 if result["strict_observation_ready"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
