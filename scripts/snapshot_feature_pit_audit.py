"""Freeze only requested auxiliary records and label-free race/runner keys.

The live DB is read-only in one consistent transaction. No historical speed
formula is invented, and post-2022 race outcomes are never projected.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "2"
from run_market_edge_bet_types import protect
from verify_market_segments_outputs import verify_files, read, sha256, require

OUT = ROOT / "reports/generated/data-quality/feature-pit-source-v1"


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def protected():
    state = protect()
    prior = ROOT / "reports/generated/model-experiments/h04-bet-types-v1"
    manifest = read(prior / "artifact_manifest.json")
    state["bet_types_files_verified"] = verify_files(prior, manifest["files"])
    state["bet_types_manifest_sha256"] = sha256(prior / "artifact_manifest.json")
    verify_files(ROOT, manifest["frozen_source_sha256"])
    verify_files(ROOT, manifest["additional_source_sha256"])
    return state


def main():
    require(not OUT.exists(), "snapshot destination already exists; never overwrite")
    before = protected()
    OUT.mkdir(parents=True)
    source_path = ROOT / "keiba/data/keiba_ultimate.db"
    path = OUT / "audit-source.sqlite"
    info = {"schema": "feature-pit-label-free-source-v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source_path), "read_mode": "mode=ro; query_only; one BEGIN read transaction",
            "protected_before": before, "table_audit": {}, "script_sha256": sha256(Path(__file__)),
            "race_outcomes_projected": False, "auxiliary_values_for_audit_only": True}
    src = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    dst = sqlite3.connect(path)
    try:
        src.execute("PRAGMA query_only=ON")
        src.execute("BEGIN")
        tables = dict(src.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        info["source_schema"] = tables
        projections = {
            "training_data": "SELECT * FROM training_data ORDER BY id",
            "speed_figures": "SELECT * FROM speed_figures ORDER BY id",
            "races_ultimate": """SELECT race_id, created_at,
                json_valid(data) AS data_json_valid,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.race_id') END AS inner_race_id,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.date') END AS race_date,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.race_name') END AS race_name,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.venue') END AS venue,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.source_host') END AS source_host,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.distance') END AS distance,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.surface') END AS surface,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.track_type') END AS track_type,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.field_condition') END AS field_condition,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.race_class') END AS race_class,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.num_horses') END AS num_horses,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.post_time') END AS post_time
                FROM races_ultimate ORDER BY race_id""",
            "race_results_ultimate": """SELECT id AS source_row_id, race_id, created_at,
                json_valid(data) AS data_json_valid,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.race_id') END AS inner_race_id,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.horse_id') END AS horse_id,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.horse_number') END AS horse_number,
                CASE WHEN json_valid(data) THEN json_extract(data,'$.horse_name') END AS horse_name
                FROM race_results_ultimate ORDER BY id""",
            "races": "SELECT race_id,kaisai_date,source,created_at FROM races ORDER BY race_id",
        }
        for table, sql in projections.items():
            if table in ("training_data", "speed_figures"):
                dst.execute(tables[table])
            cursor = src.execute(sql)
            cols = [d[0] for d in cursor.description]
            if table not in ("training_data", "speed_figures"):
                # No declared affinity: preserve the source JSON's native types.
                dst.execute('CREATE TABLE "' + table + '" (' + ','.join('"' + c + '"' for c in cols) + ')')
            insert = 'INSERT INTO "' + table + '" VALUES (' + ','.join('?' for _ in cols) + ')'
            digest = hashlib.sha256()
            count = 0
            while rows := cursor.fetchmany(2000):
                for row in rows:
                    digest.update((json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode())
                dst.executemany(insert, rows)
                count += len(rows)
            expected = src.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
            require(count == expected, "projection lost rows: " + table)
            info["table_audit"][table] = {"rows": count, "columns": cols, "sql": sql, "logical_rows_sha256": digest.hexdigest()}
            print(table, count, flush=True)
        dst.execute("CREATE INDEX audit_race_key ON races_ultimate(race_id)")
        dst.execute("CREATE INDEX audit_runner_key ON race_results_ultimate(race_id,horse_number)")
        dst.commit()
        require(dst.execute("PRAGMA quick_check").fetchone()[0] == "ok", "snapshot integrity error")
    finally:
        src.rollback()
        src.close()
        dst.close()
    info["snapshot"] = {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
    info["protected_after"] = protected()
    write(OUT / "snapshot-manifest.json", info)
    print(json.dumps(info["snapshot"], indent=2))


if __name__ == "__main__":
    main()
