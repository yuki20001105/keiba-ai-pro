"""Finalize isolated provenance audits; does not reconstruct, fit or deploy."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "python-api"), str(ROOT / "keiba")]
from snapshot_feature_pit_audit import protected, write
from verify_market_segments_outputs import require, sha256, read, verify_files

SPEED = ROOT / "reports/generated/model-experiments/speed-figure-pit-v1"
TRAIN = ROOT / "reports/generated/model-experiments/training-data-pit-audit-v1"
SOURCE = ROOT / "reports/generated/data-quality/feature-pit-source-v1"


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "artifact_manifest.json":
            require(not path.is_symlink() and path.resolve().is_relative_to(root), "artifact escapes root")
            result[path.relative_to(root).as_posix()] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return result


def main():
    for folder in (SPEED, TRAIN, SOURCE):
        require(not (folder / "artifact_manifest.json").exists(), "existing final manifest; no overwrite")
    for folder in (SPEED, TRAIN):
        proof = read(folder / "independent-verification.json")
        require(proof.get("verified") is True and proof.get("issues") == [], "independent verification required")
        verified = dict(proof.get("verified_artifact_sha256", proof.get("output_sha256", {})))
        if (folder / "report-review.json").exists():
            review = read(folder / "report-review.json")
            require(review["verified"] is True and review["issues"] == [], "report review failed")
            require(review["independent_verification_sha256"] == sha256(folder / "independent-verification.json"), "review proof binding changed")
            require(set(review["files"]) == {"summary.md", "audit.md"}, "only narrative reports may be reviewed after receipt")
            require(review["prior_report_sha256"] == {name: verified[name] for name in review["files"]}, "prior report binding mismatch")
            verified.update(review["files"])
        verify_files(folder, verified)
        require((folder / "summary.md").exists() and (folder / "audit.md").exists(), "final reports required")
        require(not (folder / "test-run.json").exists() and not (folder / "closure-check.json").exists(), "closure attempt exists")
    source = read(SOURCE / "snapshot-manifest.json")
    require(sha256(Path(source["snapshot"]["path"])) == source["snapshot"]["sha256"], "audit source changed")
    sf_plan = read(SPEED / "feasibility-plan.json")
    require(sha256(SPEED / "speed_figure_formula_audit.json") == sf_plan["formula_sha256"], "formula audit changed")
    require(sha256(ROOT / "scripts/audit_speed_figure_feasibility.py") == sf_plan["code_sha256"], "speed feasibility code changed")
    sf_proof = read(SPEED / "independent-verification.json")
    require(sf_proof["snapshot_sha256"] == source["snapshot"]["sha256"], "speed verification source mismatch")
    require(hashlib.sha256(json.dumps(sf_proof["verification_source"]["sql"], ensure_ascii=True,
        separators=(",", ":")).encode()).hexdigest() == sf_proof["verification_source_sha256"], "SQL verification recipe changed")
    require(read(SPEED / "feasibility-result.json")["RECONSTRUCTION_VALIDATED"] is False, "unexpected reconstruction")
    tr_plan = read(TRAIN / "audit_plan.json")
    tr_result = read(TRAIN / "training-audit-result.json")
    tr_proof = read(TRAIN / "independent-verification.json")
    require(sha256(TRAIN / "training-audit-result.json") == tr_proof["verified_result_sha256"], "training result changed")
    require(sha256(ROOT / "scripts/verify_training_data_pit_audit.py") == tr_proof["verifier_sha256"], "training verifier changed")
    require(tr_proof["source_sha256"] == tr_plan["snapshot_sha256"] == source["snapshot"]["sha256"], "training snapshot changed")
    require(sha256(TRAIN / "audit_plan.json") == tr_result["audit_plan_sha256"], "training plan changed")
    require(sha256(TRAIN / "training_source_audit.json") == tr_plan["source_audit_sha256"], "training source audit changed")
    require(sha256(SOURCE / "snapshot-manifest.json") == tr_plan["snapshot_manifest_sha256"], "snapshot manifest changed")
    require(tr_result["model_training_performed"] is False and tr_result["safe_point_in_time_records"] == 0, "unexpected training admission")
    verify_files(TRAIN, tr_result["files"])
    verify_files(ROOT, tr_plan["source_sha256"])
    for item in tr_plan["cohorts"].values():
        require(sha256(item["path"]) == item["sha256"], "research cohort changed")
    before = protected()
    tests = [f"python-api/tests/test_market_quality_{name}.py" for name in ["acquisition", "lineage", "observations", "integrity", "release"]]
    tests += [f"python-api/tests/test_market_segments_{name}.py" for name in ["data", "diagnostics", "modeling", "evaluation", "worker", "finish", "output_verification"]]
    tests += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "python-api/tests").glob("test_bet_types_*.py"))]
    tests += ["python-api/tests/test_h04_bet_types_verification.py"]
    tests += ["python-api/tests/" + name for name in ["test_speed_figure_feasibility.py", "test_speed_figure_provenance.py",
               "test_training_data_provenance.py", "test_training_pit_verification.py"]]
    require(all((ROOT / name).exists() for name in tests), "required regression test missing")
    command = [str(ROOT / "python-api/.venv/Scripts/python.exe"), "-m", "pytest", *tests, "-q"]
    env = dict(os.environ)
    env.update(PYTHONPATH=str(ROOT / "python-api") + os.pathsep + str(ROOT / "keiba"), PYTHONIOENCODING="utf-8",
        OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2")
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=150)
    passed = re.search(r"(\d+) passed", run.stdout)
    record = {"created_at": datetime.now(timezone.utc).isoformat(), "command": command, "returncode": run.returncode,
              "stdout": run.stdout, "stderr": run.stderr, "test_sha256": {p: sha256(ROOT / p) for p in tests}}
    for folder in (SPEED, TRAIN):
        write(folder / "test-run.json", record)
    require(run.returncode == 0 and passed and int(passed.group(1)) == 545, "all 545 regressions must pass")
    after = protected()
    additional = [*tests, "scripts/snapshot_feature_pit_audit.py", "scripts/audit_speed_figure_feasibility.py",
                  "scripts/close_feature_pit_audits.py", "scripts/audit_speed_figure_provenance.py",
                  "scripts/audit_training_data_provenance.py", "scripts/verify_training_data_pit_audit.py"]
    additional += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "python-api/feature_provenance").glob("*.py"))]
    source_hashes = {p: sha256(ROOT / p) for p in additional}
    checks = {"created_at": datetime.now(timezone.utc).isoformat(), "protected_before": before, "protected_after": after,
        "models_fitted": 0, "H04_changed": False, "TrackA_changed": False, "source_DB_mutated": False,
        "commit_push": False, "actual_purchases": 0, "snapshot_sha256": source["snapshot"]["sha256"],
        "tests_passed": int(passed.group(1)), "status": "audit_complete_model_admission_not_passed"}
    for folder in (SPEED, TRAIN):
        write(folder / "closure-check.json", checks)
    require(sha256(Path(source["snapshot"]["path"])) == source["snapshot"]["sha256"], "audit input mutated during tests")
    for folder in (SOURCE, SPEED, TRAIN):
        files = inventory(folder)
        write(folder / "artifact_manifest.json", {"schema": "isolated-feature-provenance-manifest-v1",
            "created_at": datetime.now(timezone.utc).isoformat(), "files": files, "code_and_tests_sha256": source_hashes,
            "external_snapshot_manifest": {"path": str(SOURCE / "snapshot-manifest.json"), "sha256": sha256(SOURCE / "snapshot-manifest.json")},
            "no_model_evaluation": True, "status": "audit_complete_gated_no_model_fit"})
        verify_files(folder, files)
    verify_files(ROOT, source_hashes)
    print(json.dumps({"sealed": True, "tests_passed": int(passed.group(1)), "collector_healthy": after["collector"]["healthy"],
        "manifests": {folder.name: sha256(folder / "artifact_manifest.json") for folder in (SOURCE, SPEED, TRAIN)}}, indent=2))


if __name__ == "__main__":
    main()
