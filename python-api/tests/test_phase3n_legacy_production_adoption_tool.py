from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "security" / "render_phase3n_legacy_production_adoption_sql.py"
PREFLIGHT_SCRIPT = ROOT / "scripts" / "security" / "render_phase3n_legacy_production_preflight_sql.py"
CONTRACT = ROOT / "reports" / "phase3n_legacy_production_adoption_contract_20260816.json"
CANDIDATE = "86a2d314a641160e852d3597396aadcd03e81347"


def _module():
    spec = importlib.util.spec_from_file_location("phase3n_legacy_adoption_tool_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _preflight_module():
    spec = importlib.util.spec_from_file_location("phase3n_legacy_preflight_tool_test", PREFLIGHT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_observed_contract_is_approved_exact_candidate_and_data_preserving() -> None:
    contract = _contract()

    assert contract["candidate_commit_sha"] == CANDIDATE
    assert contract["migration_apply_authorized"] is True
    assert contract["disposable_clone_test_authorized"] is True
    assert contract["clone_test_approval_reference"] == (
        "codex-user-instruction-2026-08-16-preview-clone-validation"
    )
    assert contract["review_status"] == "approved"
    assert contract["approval_reference"] == (
        "codex-user-instruction-2026-08-22-production-migration-approval"
    )
    assert contract["source_row_counts"]["race_results"] == 9588
    assert contract["source_row_counts"]["race_payouts"] == 5847
    assert contract["source_row_counts"]["race_results_ultimate"] == 719
    assert contract["preflight_facts"]["ultimate_missing_horse_number_after_decode"] == 0
    assert contract["preflight_facts"]["ultimate_duplicate_race_horse_after_decode"] == 0
    assert contract["preflight_facts"]["metadata_path_without_object"] == 0
    assert contract["legacy_domain_user_reference_policy"]["action"] == (
        "set_missing_profile_reference_to_null_in_canonical_copy"
    )
    assert contract["legacy_domain_user_reference_policy"]["archive_preserves_original"] is True
    assert contract["legacy_domain_user_reference_policy"]["tables"]["races"] == {
        "rows_without_profile": 288,
        "distinct_user_ids_without_profile": 1,
        "rows_without_auth": 288,
        "expected_canonical_null_rows": 288,
    }
    assert contract["data_preservation_contract"] == {
        "delete_source_rows": False,
        "drop_source_tables": False,
        "truncate_source_tables": False,
        "archive_source_tables_in_same_transaction": True,
        "verify_source_counts_before_and_after": True,
        "verify_source_digests_before_apply": True,
        "copy_only_allowlisted_tables": True,
        "preserve_legacy_users_table_in_archive_only": True,
        "preserve_auth_schema_in_place": True,
        "preserve_storage_objects_in_place": True,
    }


def test_review_sql_is_full_but_unconditionally_aborts_before_first_change() -> None:
    module = _module()
    raw, digest = module._load_contract(CONTRACT)
    contract = module._validated_contract(raw, expected_commit=CANDIDATE)
    head = module.subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    manifest = module.RUNNER.load_ancestor_manifest(
        module.DEFAULT_MANIFEST,
        ancestor_commit=CANDIDATE,
        expected_head_commit=head,
    )

    sql = module.build_adoption_transaction(
        contract=contract,
        contract_sha256=digest,
        manifest=manifest,
        candidate_commit=CANDIDATE,
        review_only=True,
    )

    guard = sql.index("phase3n-legacy-adoption-review-only-not-authorized")
    first_change = sql.index("CREATE SCHEMA phase3n_legacy_20260816")
    assert sql.startswith("BEGIN;\n")
    assert sql.endswith("COMMIT;\n")
    assert guard < first_change
    assert sql.count("-- canonical migration ") == 21
    assert "ALTER TABLE public.race_results SET SCHEMA phase3n_legacy_20260816" in sql
    assert "(data #>> '{}')::JSONB" in sql
    assert "phase3m_internal.bootstrap_history" in sql
    assert "phase3n_prediction_observations" in sql
    assert "phase3n-legacy-user-reference-contract-changed:races" in sql
    assert "phase3n-legacy-canonical-user-reference-invalid:races" in sql
    assert "THEN source_rows.user_id ELSE NULL END" in sql
    upper = sql.upper()
    assert "DROP TABLE" not in upper
    assert "TRUNCATE TABLE" not in upper
    assert "DELETE FROM" not in upper


def test_pending_contract_cannot_render_an_approved_apply_bundle(tmp_path: Path) -> None:
    module = _module()
    contract = _contract()
    contract["review_status"] = "pending"
    contract["migration_apply_authorized"] = False
    contract["approval_reference"] = None
    path = ROOT / "reports" / "phase3n-legacy-adoption-pending-test.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    try:
        with pytest.raises(module.RenderFailure, match="production-migration-approval-not-recorded"):
            module.render_bundle(
                contract_path=path,
                manifest_path=module.DEFAULT_MANIFEST,
                expected_commit=CANDIDATE,
                output_path=ROOT / "reports" / "phase3n-adoption-should-not-exist.sql",
                approval_digest="0" * 64,
                approval_reference="not-authorized",
                target_scope="production",
                target_project_ref="grfwkutcsavqicaimssn",
            )
    finally:
        path.unlink(missing_ok=True)


def test_approved_contract_requires_exact_contract_digest_and_reference() -> None:
    module = _module()
    contract = _contract()
    contract["review_status"] = "approved"
    contract["migration_apply_authorized"] = True
    contract["approval_reference"] = "test-review"
    path = ROOT / "reports" / "phase3n-legacy-adoption-approved-test.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    try:
        with pytest.raises(module.RenderFailure, match="approval-digest-mismatch"):
            module.render_bundle(
                contract_path=path,
                manifest_path=module.DEFAULT_MANIFEST,
                expected_commit=CANDIDATE,
                output_path=ROOT / "reports" / "phase3n-adoption-should-not-exist.sql",
                approval_digest="0" * 64,
                approval_reference="test-review",
                target_scope="production",
                target_project_ref="grfwkutcsavqicaimssn",
            )
    finally:
        path.unlink(missing_ok=True)


def test_exact_approved_contract_can_render_apply_bundle_without_review_guard() -> None:
    module = _module()
    contract = _contract()
    contract["review_status"] = "approved"
    contract["migration_apply_authorized"] = True
    contract["approval_reference"] = "test-review"
    path = ROOT / "reports" / "phase3n-legacy-adoption-approved-test.json"
    output = ROOT / "reports" / "phase3n-legacy-adoption-approved-test.sql"
    path.write_text(json.dumps(contract), encoding="utf-8")
    try:
        _loaded, digest = module._load_contract(path)
        result = module.render_bundle(
            contract_path=path,
            manifest_path=module.DEFAULT_MANIFEST,
            expected_commit=CANDIDATE,
            output_path=output,
            approval_digest=digest,
            approval_reference="test-review",
            target_scope="production",
            target_project_ref="grfwkutcsavqicaimssn",
        )
        sql = output.read_text(encoding="utf-8")

        assert result["review_only"] is False
        assert result["migration_apply_authorized"] is True
        assert "phase3n-legacy-adoption-review-only-not-authorized" not in sql
        assert "CREATE SCHEMA phase3n_legacy_20260816" in sql
    finally:
        path.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def test_exact_clone_authorization_renders_only_for_distinct_disposable_project() -> None:
    module = _module()
    contract = _contract()
    output = ROOT / "reports" / "phase3n-legacy-adoption-clone-test.sql"
    _loaded, digest = module._load_contract(CONTRACT)
    clone_ref = "abcdefghijklmnopqrst"
    try:
        result = module.render_bundle(
            contract_path=CONTRACT,
            manifest_path=module.DEFAULT_MANIFEST,
            expected_commit=CANDIDATE,
            output_path=output,
            approval_digest=digest,
            approval_reference=contract["clone_test_approval_reference"],
            target_scope="disposable-clone",
            target_project_ref=clone_ref,
        )
        sql = output.read_text(encoding="utf-8")

        assert result["review_only"] is False
        assert result["target_scope"] == "disposable-clone"
        assert result["target_project_ref"] == clone_ref
        assert result["migration_apply_authorized"] is True
        assert "phase3n-legacy-adoption-review-only-not-authorized" not in sql
        assert "-- execution target scope disposable-clone" in sql
        assert f"-- execution target project ref {clone_ref}" in sql
    finally:
        output.unlink(missing_ok=True)


@pytest.mark.parametrize("target_ref", ("grfwkutcsavqicaimssn", "bad"))
def test_clone_bundle_rejects_production_or_invalid_project_ref(target_ref: str) -> None:
    module = _module()
    contract = _contract()
    _loaded, digest = module._load_contract(CONTRACT)

    with pytest.raises(module.RenderFailure, match="disposable-clone-project-ref-invalid"):
        module.render_bundle(
            contract_path=CONTRACT,
            manifest_path=module.DEFAULT_MANIFEST,
            expected_commit=CANDIDATE,
            output_path=ROOT / "reports" / "phase3n-adoption-should-not-exist.sql",
            approval_digest=digest,
            approval_reference=contract["clone_test_approval_reference"],
            target_scope="disposable-clone",
            target_project_ref=target_ref,
        )


def test_clone_rollback_probe_is_after_archive_and_before_migrations() -> None:
    module = _module()
    contract = _contract()
    _loaded, digest = module._load_contract(CONTRACT)
    output = ROOT / "reports" / "phase3n-legacy-adoption-clone-rollback-test.sql"
    try:
        result = module.render_bundle(
            contract_path=CONTRACT,
            manifest_path=module.DEFAULT_MANIFEST,
            expected_commit=CANDIDATE,
            output_path=output,
            approval_digest=digest,
            approval_reference=contract["clone_test_approval_reference"],
            target_scope="disposable-clone",
            target_project_ref="abcdefghijklmnopqrst",
            clone_fault_after_archive=True,
        )
        sql = output.read_text(encoding="utf-8")
        archive = sql.index("CREATE SCHEMA phase3n_legacy_20260816")
        fault = sql.index("phase3n-disposable-clone-forced-rollback-after-archive")
        first_migration = sql.index("-- canonical migration ")

        assert result["clone_fault_after_archive"] is True
        assert archive < fault < first_migration
    finally:
        output.unlink(missing_ok=True)


def test_clone_fault_injection_is_rejected_outside_disposable_clone() -> None:
    module = _module()

    with pytest.raises(module.RenderFailure, match="clone-fault-injection-scope-invalid"):
        module.render_bundle(
            contract_path=CONTRACT,
            manifest_path=module.DEFAULT_MANIFEST,
            expected_commit=CANDIDATE,
            output_path=ROOT / "reports" / "phase3n-adoption-should-not-exist.sql",
            approval_digest=None,
            approval_reference=None,
            clone_fault_after_archive=True,
        )


@pytest.mark.parametrize(
    ("mutation", "failure"),
    (
        (lambda value: value["source_row_counts"].__setitem__("races", -1), "contract-row-counts-invalid"),
        (lambda value: value["source_row_digests"].__setitem__("races", "bad"), "contract-row-digests-invalid"),
        (lambda value: value["preflight_facts"].__setitem__("race_results_without_race", 1), "contract-preflight-facts-invalid"),
        (
            lambda value: value["legacy_domain_user_reference_policy"].__setitem__("action", "copy-as-is"),
            "contract-user-reference-policy-invalid",
        ),
        (lambda value: value.__setitem__("candidate_commit_sha", "f" * 40), "contract-candidate-mismatch"),
    ),
)
def test_contract_tampering_fails_closed(mutation, failure: str) -> None:
    module = _module()
    value = copy.deepcopy(_contract())
    mutation(value)

    with pytest.raises(module.RenderFailure, match=failure):
        module._validated_contract(value, expected_commit=CANDIDATE)


def test_tool_is_offline_and_confines_outputs_to_reports() -> None:
    module = _module()
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"remote_connection_attempted": False' in source
    assert '"production_changed": False' in source
    assert module._safe_report_path(
        ROOT / "reports" / "phase3n-adoption-test.sql",
        suffix=".sql",
    ) == (ROOT / "reports" / "phase3n-adoption-test.sql").resolve()
    with pytest.raises(module.RenderFailure, match="output-must-be-under-reports"):
        module._safe_report_path(ROOT / "phase3n-adoption.sql", suffix=".sql")
    for forbidden in ("psycopg", "DATABASE_URL", "PGPASSWORD", "supabase db push"):
        assert forbidden not in source


def test_standalone_preflight_is_repeatable_read_and_contains_no_mutation() -> None:
    module = _module()
    preflight = _preflight_module()
    raw, digest = module._load_contract(CONTRACT)
    contract = module._validated_contract(raw, expected_commit=CANDIDATE)

    sql = preflight.build_read_only_preflight(
        contract=contract,
        contract_sha256=digest,
    )

    assert sql.startswith("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;\n")
    assert "'transaction_read_only', current_setting('transaction_read_only')" in sql
    assert "$phase3n_legacy_preflight$" in sql
    assert "array_agg(c.relname::TEXT ORDER BY c.relname::TEXT)" in sql
    assert sql.endswith("COMMIT;\n")
    upper = sql.upper()
    for forbidden in (
        "CREATE TABLE",
        "CREATE SCHEMA",
        "ALTER TABLE",
        "DROP ",
        "TRUNCATE ",
        "DELETE FROM",
        "INSERT INTO",
        "UPDATE ",
        "GRANT ",
        "REVOKE ",
    ):
        assert forbidden not in upper
