from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_limited_production_observation_release.py"
CONTRACT = ROOT / "config/limited_production_observation_contract.v1.json"
SPEC = importlib.util.spec_from_file_location("limited_observation_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

COMMIT = "a" * 40


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _report(monkeypatch, contract: dict | None = None, *, attestation_valid: bool = True) -> dict:
    monkeypatch.setattr(
        gate,
        "_load_phase3n_verifier",
        lambda: SimpleNamespace(
            validate_gate_report=lambda *_args, **_kwargs: (attestation_valid, ())
        ),
    )
    return gate.build_report(
        contract or _contract(),
        {"trusted": True},
        expected_commit=COMMIT,
        expected_run_id=123,
        expected_run_attempt=1,
        expected_repository="yuki20001105/keiba-ai-pro",
        expected_repository_id="123456",
        max_age_seconds=3600,
        now=datetime.now(timezone.utc),
    )


def test_contract_separates_system_release_from_model_business_validation(monkeypatch) -> None:
    valid, failures = gate.validate_contract(_contract())
    assert valid is True and failures == ()
    report = _report(monkeypatch)
    assert report["success"] is True
    assert report["system_release_ready"] is True
    assert report["observation_release_ready"] is True
    assert report["model_business_validated"] is False
    assert report["full_production_ready"] is False
    assert report["automated_betting_allowed"] is False


def test_contract_rejects_any_automatic_betting_or_active_model_claim(monkeypatch) -> None:
    contract = _contract()
    contract["runtime_controls"]["automated_betting_enabled"] = True
    report = _report(monkeypatch, contract)
    assert report["success"] is False
    assert "contract-runtime-controls-invalid" in report["failure_codes"]

    contract = _contract()
    contract["runtime_controls"]["model_runtime_status"] = "active"
    report = _report(monkeypatch, contract)
    assert report["success"] is False
    assert "contract-runtime-controls-invalid" in report["failure_codes"]


def test_trusted_system_attestation_remains_mandatory(monkeypatch) -> None:
    report = _report(monkeypatch, attestation_valid=False)
    assert report["system_release_ready"] is False
    assert report["observation_release_ready"] is False
    assert report["full_production_ready"] is False
    assert "trusted-system-attestation-invalid" in report["failure_codes"]
