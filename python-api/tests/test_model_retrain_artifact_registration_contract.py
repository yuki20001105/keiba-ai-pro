from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase" / "migrations" / "20260802_model_retrain_artifact_registration.sql"
MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
CONTRACT = ROOT / "supabase" / "bootstrap" / "v1" / "tests" / "bootstrap_contract.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{re.escape(name)}\b(?P<body>.*?)\n\$\$;",
        _sql(),
        flags=re.DOTALL,
    )
    assert match, f"missing function: {name}"
    return match.group("body")


def test_artifact_ledger_is_private_immutable_and_not_a_promotion_path() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.model_retrain_artifacts" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE public.model_retrain_artifacts" in sql
    assert "GRANT SELECT ON TABLE public.model_retrain_artifacts TO service_role" in sql
    assert "model retrain artifact registrations are immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON public.model_retrain_artifacts" in sql
    registration = _function("register_model_retrain_artifact").lower()
    assert "activate" not in registration
    assert "evaluation" not in registration


def test_registration_requires_live_fenced_worker_and_current_approval() -> None:
    body = _function("register_model_retrain_artifact")
    for fragment in (
        "v_job.record_version <> p_expected_version",
        "v_job.job_state <> 'running'",
        "v_job.worker_id IS DISTINCT FROM p_worker_id",
        "v_job.fencing_token IS DISTINCT FROM p_fencing_token",
        "v_job.lease_expires_at <= v_now",
        "v_approval.approval_status <> 'approved'",
        "v_approval.expires_at <= v_now",
        "v_approval.job_created IS DISTINCT FROM TRUE",
        "v_approval.approved_payload_hash IS DISTINCT FROM v_job.approved_payload_hash",
    ):
        assert fragment in body


def test_registration_binds_strict_private_object_identity() -> None:
    body = _function("register_model_retrain_artifact")
    for fragment in (
        "p_object_name NOT IN",
        "p_artifact_sha256 !~ '^[0-9a-f]{64}$'",
        "p_artifact_size_bytes NOT BETWEEN 1 AND 104857600",
        "FROM storage.buckets AS b",
        "JOIN storage.objects AS o ON o.bucket_id = b.id",
        "b.id = 'models' AND b.public IS FALSE",
        "v_uri := 'models://' || p_object_name",
        "job_state = 'artifact-registered'",
        "artifact_written = TRUE",
        "'artifact-registered'",
    ):
        assert fragment in body


def test_job_guard_allows_artifact_identity_only_on_terminal_registration() -> None:
    body = _function("_guard_model_retrain_job_update")
    assert "OLD.job_state = 'running'" in body
    assert "NEW.job_state IN ('running', 'artifact-registered', 'failed')" in body
    assert "NEW.job_state = 'artifact-registered'" in body
    assert "OLD.artifact_written IS DISTINCT FROM FALSE" in body
    assert "NEW.artifact_written IS DISTINCT FROM TRUE" in body
    assert "model retrain artifact identity is immutable" in body


def test_bootstrap_executes_artifact_registration_contract() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [
        item for item in manifest["migrations"]
        if item["path"] == "supabase/migrations/20260802_model_retrain_artifact_registration.sql"
    ]
    assert len(entries) == 1
    assert entries[0]["version"] == "20260802143000"
    contract = CONTRACT.read_text(encoding="utf-8")
    for fragment in (
        "FROM public.register_model_retrain_artifact(",
        "phase3m model retrain artifact registration contract failed",
        "FROM public.model_retrain_artifacts",
        "phase3m_check:model_retrain_artifact_registration",
    ):
        assert fragment in contract
