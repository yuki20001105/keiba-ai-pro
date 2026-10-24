"""Verify and close the new rank-only early-stopped study without refitting."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python-api"), str(ROOT / "keiba"), str(ROOT / "scripts")]
from run_market_edge_bet_types import OUTPUT, verify, protect, read, write
from market_quality.lineage import sha256
from verify_market_segments_outputs import require, verify_files

PLAN_SHA = "b76da9379ba6bea8ec866e067d030603f79a46117af29e4bfd12b8ed4dfef107"
EXCLUDED = {"artifact_manifest.json", "job_ledger.sqlite", "job_ledger.sqlite-wal", "job_ledger.sqlite-shm", "job_ledger.sqlite-journal"}


def ledger():
    with sqlite3.connect((OUTPUT / "job_ledger.sqlite").as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(tables == {"meta", "jobs", "events", "telemetry"}, "unexpected ledger structure")
        result = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in sorted(tables)}
    jobs = result["jobs"]
    require(len(jobs) == 1 and jobs[0]["job_id"] == "rank-baseline" and jobs[0]["status"] == "completed", "one terminal baseline job required")
    require(jobs[0]["result_sha256"] == sha256(OUTPUT / "baseline-result.json"), "completed job result changed")
    return result


def inventory():
    files = {}
    for path in sorted(OUTPUT.rglob("*")):
        if path.is_file():
            require(not path.is_symlink() and path.resolve().is_relative_to(OUTPUT), "output outside research root")
            name = path.relative_to(OUTPUT).as_posix()
            if name not in EXCLUDED:
                files[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return files


def main():
    require(not (OUTPUT / "artifact_manifest.json").exists() and not (OUTPUT / "test-run.json").exists(), "no overwrite/reseal")
    plan = verify(PLAN_SHA)
    proof_path = OUTPUT / "independent-verification.json"
    proof = read(proof_path)
    require(proof["verified"] is True and proof["issues"] == [] and proof["plan_sha256"] == PLAN_SHA, "independent verification required")
    require(proof["source_script_sha256"] == sha256(ROOT / "scripts/verify_h04_bet_types.py"), "verifier source changed")
    require(proof["result_sha256"] == sha256(OUTPUT / "baseline-result.json"), "result no longer independently verified")
    proof_sha = sha256(proof_path)
    before = protect()
    jobs = ledger()
    tests = [f"python-api/tests/test_market_quality_{name}.py" for name in ["acquisition", "lineage", "observations", "integrity", "release"]]
    tests += [f"python-api/tests/test_market_segments_{name}.py" for name in ["data", "diagnostics", "modeling", "evaluation", "worker", "finish", "output_verification"]]
    tests += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "python-api/tests").glob("test_bet_types_*.py"))]
    tests += ["python-api/tests/test_h04_bet_types_verification.py"]
    env = dict(os.environ)
    env.update(PYTHONPATH=str(ROOT / "python-api") + os.pathsep + str(ROOT / "keiba"), PYTHONIOENCODING="utf-8",
               OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2")
    command = [str(ROOT / "python-api/.venv/Scripts/python.exe"), "-m", "pytest", *tests, "-q"]
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    write(OUTPUT / "test-run.json", {"created_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
        "test_sha256": {name: sha256(ROOT / name) for name in tests}})
    require(completed.returncode == 0 and "487 passed" in completed.stdout, "expected 487 tests must pass")
    require(ledger() == jobs, "terminal ledger changed")
    write(OUTPUT / "ledger-final.json", {"schema": "readonly-committed-ledger-tables-v1", "tables": jobs})
    verify(PLAN_SHA)
    require(sha256(proof_path) == proof_sha, "verification receipt changed")
    after = protect()
    write(OUTPUT / "closure-check.json", {"created_at": datetime.now(timezone.utc).isoformat(), "protected_before": before,
        "protected_after": after, "plan_sha256": PLAN_SHA, "independent_verification_sha256": proof_sha,
        "H04_fits": 0, "real_calibrator_fits": 0, "nonwin_EV_and_ROI_evaluated": False,
        "post_2022_outcomes_decoded": False, "actual_purchases": 0, "commit_push": False,
        "status": "stopped_at_preregistered_rank_gate"})
    files = inventory()
    sources = {name: sha256(ROOT / name) for name in [*tests, "scripts/verify_h04_bet_types.py", "scripts/seal_h04_bet_types.py"]}
    require(files == inventory(), "artifacts changed while closing")
    write(OUTPUT / "artifact_manifest.json", {"schema": "h04-bet-types-final-manifest-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": PLAN_SHA, "independent_verification_sha256": proof_sha, "files": files,
        "additional_source_sha256": sources, "frozen_source_sha256": plan["source_sha256"], "excluded": sorted(EXCLUDED),
        "ledger_policy": "committed read-only tables in ledger-final.json; live SQLite and sidecars excluded",
        "status": "NO_PROMISING_FIXED_PHASE_A_RULE; empirical_PL_EV_PhaseBC_not_executed"})
    verify_files(OUTPUT, files)
    verify_files(ROOT, sources)
    print(json.dumps({"sealed": True, "files": len(files), "manifest_sha256": sha256(OUTPUT / "artifact_manifest.json"),
                      "tests": "487 passed", "collector_healthy": after["collector"]["healthy"]}, indent=2))


if __name__ == "__main__":
    main()
