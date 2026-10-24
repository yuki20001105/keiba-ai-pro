"""Repeatable local release checks, without training, scraping or purchases.

This is not the protected Production release gate or a profitability verdict.
Only explicitly listed fixture E2E tests may run; real-workflow is never selected.
Each invocation preserves its own logs and report instead of overwriting evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PYTHON_TESTS = (
    "python-api/tests/test_prediction_compute.py",
    "python-api/tests/test_prediction_explainability.py",
    "python-api/tests/test_prediction_history_loader.py",
    "python-api/tests/test_prediction_input_contract.py",
    "python-api/tests/test_model_predictor_legacy_contract.py",
    "python-api/tests/test_predict_data_refresh.py",
    "python-api/tests/test_race_entry_fallback.py",
    "python-api/tests/test_betting_value_policy.py",
    "python-api/tests/test_bet_export_policy.py",
    "python-api/tests/test_betting_performance.py",
    "python-api/tests/test_performance_routes.py",
    "python-api/tests/test_phase2_authz.py",
    "python-api/tests/test_automated_betting_guard.py",
    "python-api/tests/test_local_retrain.py",
    "python-api/tests/test_phase2_authz_matrix_gate.py",
    "python-api/tests/test_phase3l_deployment_safety.py",
    "python-api/tests/test_forward_hash_compatibility_audit.py",
    "python-api/tests/test_forward_paper_compatible_report.py",
    "python-api/tests/test_active_model_input_compatibility_audit.py",
    "python-api/tests/test_local_release_checks.py",
    "python-api/tests/test_local_release_readonly_smoke.py",
    "python-api/tests/test_local_authenticated_readonly_smoke.py",
    "python-api/tests/test_local_candidate_input.py",
    "python-api/tests/test_local_candidate_evaluation.py",
    "python-api/tests/test_local_candidate_serving.py",
    "python-api/tests/test_local_candidate_serving_smoke.py",
    "python-api/tests/test_db_shared_features.py",
    "python-api/tests/test_db_shared_fit.py",
    "python-api/tests/test_db_shared_serving.py",
    "python-api/tests/test_benchmark_shared_db_fit.py",
    "python-api/tests/test_local_workflow_proof.py",
    "python-api/tests/test_reference_bets.py",
    "python-api/tests/test_server_scrape_batch.py",
    "python-api/tests/test_scrape_storage_checkpoint.py",
    "python-api/tests/test_scrape_job_restart.py",
    "python-api/tests/test_phase3e_scrape_job_security.py",
    "python-api/tests/test_local_retrain_worker_main.py",
    "python-api/tests/test_model_training_guard.py",
    "python-api/tests/test_model_deletion_guard.py",
    "python-api/tests/test_local_release_pytest_runner.py",
    "keiba/keiba_ai/tests/test_prediction_history_performance.py",
    "keiba/keiba_ai/tests/test_point_in_time_history.py",
    "keiba/keiba_ai/tests/test_feature_history_leakage.py",
    "keiba/keiba_ai/tests/test_feature_engineering.py",
)
FIXTURE_E2E = (
    "e2e/public-fixture-smoke.spec.ts", "e2e/predict-batch.spec.ts",
    "e2e/dashboard.spec.ts", "e2e/release-core-flows.spec.ts",
)


def check_commands(node: str, *, build: bool, e2e: bool) -> list[tuple[str, list[str]]]:
    checks = [
        ("frontend-unit", [node, "node_modules/vitest/vitest.mjs", "run", "--maxWorkers=2"]),
        ("python-contracts", [sys.executable, "-X", "utf8", "scripts/run_local_release_pytests.py", *PYTHON_TESTS]),
        ("typescript", [node, "node_modules/typescript/bin/tsc", "--noEmit"]),
        ("lint", [node, "node_modules/eslint/bin/eslint.js", "src/**/*.{js,jsx,ts,tsx}", "scripts/**/*.ts", ".github/skills/**/*.ts"]),
        ("authorization-matrix", [sys.executable, "-X", "utf8", "-c",
            "import sys,json; sys.path.insert(0,'scripts/security'); "
            "from generate_phase2_authz_matrix import build_matrix_payload; "
            "p=build_matrix_payload(); print(json.dumps(p['gate'])); sys.exit(0 if p['gate']['pass'] else 1)"]),
    ]
    if build:
        checks.append(("isolated-build", [node, "node_modules/next/dist/bin/next", "build", "--webpack"]))
    if e2e:
        checks.append(("fixture-e2e", [node, "node_modules/@playwright/test/cli.js", "test", *FIXTURE_E2E,
            "--workers=1", "--reporter=list", "--retries=0"]))
    return checks


def source_fingerprint() -> str:
    """Bind checks to actual tracked AND untracked code, not just a dirty HEAD."""
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    digest = hashlib.sha256()
    extensions = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".ps1", ".yml", ".yaml"}
    for name in sorted(set(paths)):
        path = ROOT / name
        if path.is_file() and path.suffix in extensions and not path.name.startswith(".env"):
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_environment(name: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if name in {"isolated-build", "fixture-e2e"}:
        env["NEXT_RELEASE_CHECK"] = "1"
    if name == "fixture-e2e":
        env.update({
            "PW_USE_WEBSERVER": "1", "PW_PORT": "3101", "PW_BASE_URL": "http://127.0.0.1:3101",
            "NEXT_PUBLIC_SUPABASE_URL": "http://127.0.0.1:54321", "SUPABASE_URL": "http://127.0.0.1:54321",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": "e2e-dummy-anon-key",
            "SUPABASE_SERVICE_ROLE_KEY": "e2e-dummy-service-role-key",
        })
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build in build/release-check, not the active .next.")
    parser.add_argument("--e2e", action="store_true", help="Run only allowlisted fixture tests on loopback port 3101.")
    args = parser.parse_args()
    node = shutil.which("node")
    if not node:
        parser.error("Node.js is required.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "reports/generated/release-readiness" / stamp
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "scope": "local-automated-checks-only", "started_at": stamp,
        "production_release_authorized": False, "profitability_validated": False,
        "real_purchase_enabled": False, "model_activation_performed": False,
        "source_sha256_before": source_fingerprint(), "checks": [],
    }
    report_path = out / "checks.json"
    checks = check_commands(node, build=args.build, e2e=args.e2e)
    try:
        for name, command in checks:
            print(f"Checking {name}...", flush=True)
            started = time.monotonic()
            log_path = out / f"{name}.log"
            with log_path.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=ROOT, env=check_environment(name), stdout=log, stderr=subprocess.STDOUT)
            report["checks"].append({
                "name": name, "return_code": result.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "log": str(log_path.relative_to(ROOT)),
            })
            print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'} ({report['checks'][-1]['duration_seconds']}s)", flush=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        report["source_sha256_after"] = source_fingerprint()
        report["source_unchanged"] = report["source_sha256_before"] == report["source_sha256_after"]
        report["automated_checks_passed"] = (len(report["checks"]) == len(checks)
            and all(c["return_code"] == 0 for c in report["checks"])
            and report["source_unchanged"])
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence: {report_path}", flush=True)
    return 0 if report["automated_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
