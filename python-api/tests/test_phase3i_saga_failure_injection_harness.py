from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3i_saga_failure_injection_gate.py"
CONTRACT = ROOT / "python-api" / "tests" / "fixtures" / "phase3i_saga_failure_matrix_v1.json"
STATE_MACHINE = ROOT / "python-api" / "scraping" / "cross_store_saga_contract.py"
TESTED_COMMIT = "1" * 40

SPEC = importlib.util.spec_from_file_location("phase3i_saga_failure_injection_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _report(tmp_path: Path, **kwargs: object) -> tuple[int, dict]:
    report = tmp_path / "runtime.json"
    code = runner.run_gate(
        expected_commit=kwargs.pop("expected_commit", TESTED_COMMIT),
        contract_path=kwargs.pop("contract_path", CONTRACT),
        state_machine_path=kwargs.pop("state_machine_path", STATE_MACHINE),
        report_path=report,
        **kwargs,
    )
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_actual_state_machine_runs_complete_matrix_with_zero_effects(tmp_path: Path) -> None:
    code, report = _report(tmp_path)
    assert code == 0
    assert report["success"] is True
    assert report["scenario_count"] == 14
    assert all(report["scenario_checks"].values())
    assert all(report["invariant_checks"].values())
    assert report["effect_count"] == 0
    assert report["synthetic"] is True
    assert report["non_executable"] is True
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["cleanup"] == {"attempted": True, "workspace_absent": True}
    assert report["tested_commit_sha"] == TESTED_COMMIT


def test_synthetic_workspace_is_removed_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "known-workspace"

    def make_workspace(*_args: object, **_kwargs: object) -> str:
        workspace.mkdir()
        return str(workspace)

    monkeypatch.setattr(runner.tempfile, "mkdtemp", make_workspace)
    code, report = _report(tmp_path)
    assert code == 0
    assert workspace.exists() is False
    assert report["cleanup"]["workspace_absent"] is True


def test_any_recorded_forbidden_effect_fails_closed(tmp_path: Path) -> None:
    def inject_effect(_module, _ledger):
        with open(tmp_path / "forbidden-write.txt", "w", encoding="utf-8") as handle:
            handle.write("must never be written")
        raise AssertionError("effect guard did not reject a file write")

    code, report = _report(tmp_path, scenario_runner=inject_effect)
    assert code == 1
    assert report["success"] is False
    assert report["effect_count"] == 1
    assert report["production_ready"] is False
    assert report["l3_eligible"] is False
    assert report["cleanup"]["workspace_absent"] is True
    assert (tmp_path / "forbidden-write.txt").exists() is False


def test_scenario_exception_still_cleans_up_and_writes_sanitized_failure(tmp_path: Path) -> None:
    def explode(_module, _ledger):
        raise RuntimeError("do not expose this operator text")

    code, report = _report(tmp_path, scenario_runner=explode)
    assert code == 1
    assert report["success"] is False
    assert report["cleanup"] == {"attempted": True, "workspace_absent": True}
    assert "do not expose" not in json.dumps(report)


@pytest.mark.parametrize("commit", ["", "1" * 39, "G" * 40, None, True])
def test_expected_commit_is_required_and_strict(tmp_path: Path, commit: object) -> None:
    code, report = _report(tmp_path, expected_commit=commit)
    assert code == 1
    assert report["success"] is False
    assert report["tested_commit_sha"] == ""
    assert report["cleanup"]["workspace_absent"] is True


def test_missing_or_incomplete_state_machine_api_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"
    code, report = _report(tmp_path, state_machine_path=missing)
    assert code == 1
    assert report["success"] is False
    assert report["cleanup"]["workspace_absent"] is True

    incomplete = tmp_path / "incomplete.py"
    incomplete.write_text("def create_saga(binding):\n    return binding\n", encoding="utf-8")
    code, report = _report(tmp_path, state_machine_path=incomplete)
    assert code == 1
    assert report["success"] is False


def test_import_time_file_effect_is_recorded_rejected_and_never_written(tmp_path: Path) -> None:
    effect_target = tmp_path / "import-side-effect.txt"
    malicious_module = tmp_path / "effectful_state_machine.py"
    malicious_module.write_text(
        "from pathlib import Path\n"
        f"Path({str(effect_target)!r}).write_text('forbidden', encoding='utf-8')\n",
        encoding="utf-8",
    )

    code, report = _report(tmp_path, state_machine_path=malicious_module)

    assert code == 1
    assert report["success"] is False
    assert report["effect_count"] > 0
    assert effect_target.exists() is False
    assert report["cleanup"] == {"attempted": True, "workspace_absent": True}


def test_contract_schema_and_hash_are_not_self_describing(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["scenarios"].pop()
    invalid = tmp_path / "invalid-contract.json"
    invalid.write_text(json.dumps(contract), encoding="utf-8")
    code, report = _report(tmp_path, contract_path=invalid)
    assert code == 1
    assert report["success"] is False
    assert report["contract_sha256"] == ""


def test_report_write_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    code, _ = _report(tmp_path)
    assert code == 0
    assert list(tmp_path.glob(".runtime.json.*.tmp")) == []


def test_contract_and_report_key_sets_are_frozen(tmp_path: Path) -> None:
    verifier = runner._load_module(runner.VERIFIER, "phase3i_contract_verifier")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["scenarios"] == list(verifier.SCENARIO_KEYS)
    assert contract["invariants"] == list(verifier.INVARIANT_KEYS)
    code, report = _report(tmp_path)
    assert code == 0
    assert frozenset(report["scenario_checks"]) == frozenset(verifier.SCENARIO_KEYS)
    assert frozenset(report["invariant_checks"]) == frozenset(verifier.INVARIANT_KEYS)
    assert frozenset(report) == verifier.EVIDENCE_KEYS


def test_terminal_state_contract_matches_the_real_state_machine_exactly() -> None:
    verifier = runner._load_module(runner.VERIFIER, "phase3i_terminal_verifier")
    module = runner._load_module(STATE_MACHINE, "phase3i_terminal_state_machine")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["terminal_states"] == list(verifier.TERMINAL_STATE_KEYS)
    assert frozenset(contract["terminal_states"]) == frozenset(module.TERMINAL_STATES)


def test_runner_has_no_network_subprocess_or_production_runtime_imports() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import subprocess",
        "import requests",
        "import httpx",
        "from supabase",
        "import supabase",
        "routers.scrape",
        "scraping.jobs",
    ):
        assert forbidden not in source


def test_runner_never_references_the_real_scrape_job_database() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "scrape_jobs.db" not in source
    assert "SUPABASE_URL" not in source
    assert "DATABASE_URL" not in source


def test_runner_installs_dynamic_effect_guards_around_scenarios() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    for guarded in (
        '"file_open"',
        '"file_write"',
        '"sqlite_connect"',
        '"network_call"',
        '"subprocess_start"',
        '"thread_start"',
        "_forbidden_effect_guard(ledger)",
    ):
        assert guarded in source
