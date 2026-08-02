from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_execution_bundle.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION public\.get_model_retrain_execution_bundle\b"
        r"(?P<body>.*?)\n\$\$;",
        _sql(),
        flags=re.DOTALL,
    )
    assert match
    return match.group("body")


def test_execution_bundle_is_service_only_and_has_no_execution_side_effects() -> None:
    sql = _sql()
    body = _function()
    assert "SECURITY DEFINER" in body
    assert "SET search_path = public, extensions" in body
    assert "REVOKE ALL ON FUNCTION public.get_model_retrain_execution_bundle" in sql
    assert ") FROM PUBLIC, anon, authenticated;" in sql
    assert ") TO service_role;" in sql
    for forbidden in (
        "joblib",
        "storage.objects",
        "model_metadata",
        "artifact_written = TRUE",
        "execution_started = TRUE",
    ):
        assert forbidden not in body


def test_execution_bundle_rechecks_live_lease_and_approval_binding() -> None:
    body = _function()
    for fragment in (
        "v_job.record_version <> p_expected_version",
        "v_job.job_state NOT IN ('claimed', 'running')",
        "v_job.worker_id IS DISTINCT FROM p_worker_id",
        "v_job.fencing_token IS DISTINCT FROM p_fencing_token",
        "v_job.lease_expires_at <= v_now",
        "PERFORM public._expire_model_retrain_approval_if_needed",
        "v_approval.approval_status <> 'approved'",
        "v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash",
        "v_payload->>'git_commit' IS DISTINCT FROM p_candidate_commit_sha",
        "v_payload->>'active_model_id' IS DISTINCT FROM p_active_model_id",
    ):
        assert fragment in body


def test_execution_payload_is_strictly_projected_and_feature_hash_is_recomputed() -> None:
    body = _function()
    for fragment in (
        "v_payload->>'state' IS DISTINCT FROM 'preview-ready'",
        "v_payload->>'target' IS DISTINCT FROM 'win'",
        "v_payload->>'model_type' IS DISTINCT FROM 'lightgbm'",
        "v_payload->>'data_snapshot_id' = repeat('0', 64)",
        "v_train_end >= v_validation_start",
        "jsonb_array_length(v_payload->'selected_features') NOT BETWEEN 1 AND 2048",
        "NOT (v_removed <@ v_selected)",
        "extensions.digest(convert_to(v_contract_text, 'UTF8'), 'sha256')",
        "v_contract_sha256 IS DISTINCT FROM v_payload->>'feature_contract_hash'",
        "'force_sync', FALSE",
        "'use_optuna', FALSE",
    ):
        assert fragment in body
    for check in (
        "future_field_exclusion",
        "out_of_time_split",
        "active_model_immutable",
        "production_write_blocked",
        "path_input_rejected",
        "data_snapshot_bound",
        "candidate_commit_bound",
    ):
        assert f'{{"key":"{check}","status":"pass"}}' in body


def test_bootstrap_manifest_and_runtime_contract_include_execution_bundle() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_execution_bundle.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802145000"
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "phase3m model retrain execution bundle contract failed" in contract
    assert "phase3m stale model retrain execution bundle was accepted" in contract
    assert "phase3m_check:model_retrain_execution_bundle" in contract
