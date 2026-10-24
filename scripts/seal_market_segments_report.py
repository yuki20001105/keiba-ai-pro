"""Seal only the new completed campaign; never fit, edit or promote a model."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]
from verify_market_segments_outputs import sha256, read, verify_files, require, _protected_state

EXPERIMENT = ROOT / "reports/generated/model-experiments/market-segments-v1"
PLAN_SHA = "957006d104504c3fb4a68491556b8844d403efedd6e18a93bf5ed2c795a2a441"
VERIFICATION_SHA = "3c12fcd46bfaec93e6f8d51b9b59075978fa1bc625cfb57f79016db9585e151c"
VERIFIER_SHA = "33fa3a088213c0c279c9ee8dbe100ae4cb64972e72bc60db9869749436668b5b"
TESTS = [f"python-api/tests/test_market_quality_{name}.py" for name in
         ("acquisition", "lineage", "observations", "integrity", "release")]
TESTS += [f"python-api/tests/test_market_segments_{name}.py" for name in
          ("data", "diagnostics", "modeling", "evaluation", "worker", "finish", "output_verification")]
EXCLUDED = {"artifact_manifest.json", "job_ledger.sqlite", "job_ledger.sqlite-wal",
            "job_ledger.sqlite-shm", "job_ledger.sqlite-journal"}


def write_new(path, content):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)


def ledger_snapshot():
    uri = (EXPERIMENT / "job_ledger.sqlite").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(names == {"meta", "jobs", "events", "telemetry"}, "unexpected ledger tables")
        tables = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                  for name in sorted(names)}
    jobs = tables["jobs"]
    require(len(jobs) == 12 and sum(j["status"] == "completed" for j in jobs) == 11,
            "campaign is not terminal")
    require([j["job_id"] for j in jobs if j["status"] == "failed"] == ["evaluate"], "unexpected failure")
    require({r["key"]: r["value"] for r in tables["meta"]}["plan_sha256"] == PLAN_SHA, "ledger plan changed")
    return tables


def inventory():
    result = {}
    for path in sorted(EXPERIMENT.rglob("*")):
        if path.is_file():
            require(not path.is_symlink() and path.resolve().is_relative_to(EXPERIMENT), "unsafe output path")
            name = path.relative_to(EXPERIMENT).as_posix()
            if name not in EXCLUDED:
                result[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return result


def main():
    manifest_path = EXPERIMENT / "artifact_manifest.json"
    require(not manifest_path.exists() and not (EXPERIMENT / "test-run.json").exists(), "already sealed or attempted; no overwrite")
    require(sha256(ROOT / "scripts/verify_market_segments_outputs.py") == VERIFIER_SHA, "verifier changed")
    require(sha256(EXPERIMENT / "artifact-verification.json") == VERIFICATION_SHA, "independent receipt changed")
    verification = read(EXPERIMENT / "artifact-verification.json")
    require(verification["verified"] is True and verification["verified_fit_count"] == 8, "independent verification failed")
    require(sha256(EXPERIMENT / "segment_plan.json") == PLAN_SHA, "plan changed")
    plan = read(EXPERIMENT / "segment_plan.json")
    verify_files(ROOT, plan["code_sha256"])
    protected_before = _protected_state(ROOT)
    ledger = ledger_snapshot()
    environment = dict(os.environ)
    environment.update(PYTHONPATH=str(ROOT / "python-api") + os.pathsep + str(ROOT / "keiba"),
                       PYTHONIOENCODING="utf-8", OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2",
                       MKL_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2")
    command = [str(ROOT / "python-api/.venv/Scripts/python.exe"), "-m", "pytest", *TESTS, "-q"]
    completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
    write_new(EXPERIMENT / "test-run.json", {"at": datetime.now(timezone.utc).isoformat(), "command": command,
        "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
        "test_source_sha256": {name: sha256(ROOT / name) for name in TESTS}})
    require(completed.returncode == 0 and "330 passed" in completed.stdout, "test suite not passing expected 330 cases")
    require(ledger_snapshot() == ledger, "ledger changed while closing campaign")
    write_new(EXPERIMENT / "ledger-final.json", {"schema": "read-only-terminal-ledger-snapshot-v1", "tables": ledger})
    verify_files(ROOT, plan["code_sha256"])
    protected_after = _protected_state(ROOT)
    write_new(EXPERIMENT / "closure-check.json", {"created_at": datetime.now(timezone.utc).isoformat(),
        "independent_verification_sha256": VERIFICATION_SHA, "plan_sha256": PLAN_SHA,
        "protected_before": protected_before, "protected_after": protected_after,
        "new_model_fits": 0, "source_DB_writes": 0, "post_2022_outcomes_decoded": False,
        "note": "Packaging only; original failed evaluation retained; no old results or DB modified"})
    files = inventory()
    source_files = [*TESTS, "scripts/verify_market_segments_outputs.py", "scripts/run_market_edge_segments_finish.py",
                    "scripts/seal_market_segments_report.py", "scripts/verify_market_quality_release.py"]
    sources = {name: sha256(ROOT / name) for name in source_files}
    require(files == inventory(), "artifacts changed while hashing")
    require(sha256(EXPERIMENT / "segment_plan.json") == PLAN_SHA, "plan changed while sealing")
    write_new(manifest_path, {"schema": "market-segments-final-manifest-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": PLAN_SHA, "verification_sha256": VERIFICATION_SHA, "files": files,
        "additional_source_sha256": sources,
        "external_quality_hash_chain": plan["phase1_gate"],
        "excluded": sorted(EXCLUDED),
        "ledger_note": "SQLite binary and sidecars excluded; committed tables captured read-only in ledger-final.json",
        "status": "completed_early_stop_no_verified_incremental_edge"})
    verify_files(EXPERIMENT, files)
    verify_files(ROOT, sources)
    print(json.dumps({"sealed": True, "files": len(files), "manifest_sha256": sha256(manifest_path),
                      "tests": "330 passed", "collector_healthy": protected_after["collector"]["healthy"]}, indent=2))


if __name__ == "__main__":
    main()
