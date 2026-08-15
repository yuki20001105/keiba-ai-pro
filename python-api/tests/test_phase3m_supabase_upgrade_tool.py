from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "security" / "render_phase3m_supabase_upgrade_sql.py"
RUNTIME_GATE = ROOT / "scripts" / "security" / "run_phase3m_supabase_upgrade_gate.py"
OLD_BOOTSTRAP_COMMIT = "861f46c18b086578e97c15d6eaa12aed89222169"


def _current_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _module():
    spec = importlib.util.spec_from_file_location("phase3m_upgrade_tool_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifests(module):
    current_commit = _current_commit()
    candidate = module.RUNNER.load_manifest(module.DEFAULT_MANIFEST)
    applied = module.RUNNER.load_ancestor_manifest(
        module.DEFAULT_MANIFEST,
        ancestor_commit=OLD_BOOTSTRAP_COMMIT,
        expected_head_commit=current_commit,
    )
    return applied, candidate


def test_historical_manifest_loader_reads_immutable_ancestor_without_requiring_worktree_equivalence() -> None:
    module = _module()
    applied, candidate = _manifests(module)

    assert len(applied.migrations) == 11
    assert len(candidate.migrations) == 21
    assert [entry.path for entry in applied.migrations] == [
        entry.path for entry in candidate.migrations[:11]
    ]
    assert [entry.sha256 for entry in applied.migrations] == [
        entry.sha256 for entry in candidate.migrations[:11]
    ]


def test_segment_parser_requires_contiguous_exact_history_coverage() -> None:
    module = _module()
    _applied, candidate = _manifests(module)
    current_commit = _current_commit()

    segments = module._load_segments(
        [f"1:11:{OLD_BOOTSTRAP_COMMIT}"],
        candidate_manifest=candidate,
        expected_commit=current_commit,
        applied_count=11,
        manifest_path=module.DEFAULT_MANIFEST,
    )
    assert [(segment.start, segment.end, segment.commit) for segment in segments] == [
        (1, 11, OLD_BOOTSTRAP_COMMIT)
    ]

    for invalid in (
        [f"2:11:{OLD_BOOTSTRAP_COMMIT}"],
        [f"1:10:{OLD_BOOTSTRAP_COMMIT}"],
        [f"1:5:{OLD_BOOTSTRAP_COMMIT}", f"7:11:{OLD_BOOTSTRAP_COMMIT}"],
        ["1:11:not-a-commit"],
    ):
        with pytest.raises(module.RenderFailure):
            module._load_segments(
                invalid,
                candidate_manifest=candidate,
                expected_commit=current_commit,
                applied_count=11,
                manifest_path=module.DEFAULT_MANIFEST,
            )


def test_upgrade_sql_preserves_prior_rows_and_appends_only_the_manifest_suffix() -> None:
    module = _module()
    applied, candidate = _manifests(module)
    current_commit = _current_commit()
    segments = module._load_segments(
        [f"1:11:{OLD_BOOTSTRAP_COMMIT}"],
        candidate_manifest=candidate,
        expected_commit=current_commit,
        applied_count=len(applied.migrations),
        manifest_path=module.DEFAULT_MANIFEST,
    )

    sql = module.build_upgrade_transaction(
        candidate_manifest=candidate,
        candidate_commit=current_commit,
        applied_count=len(applied.migrations),
        segments=segments,
    )

    assert sql.startswith("BEGIN;\n")
    assert sql.endswith("COMMIT;\n")
    assert "$phase3m_upgrade_preflight$" in sql
    assert "$phase3m_upgrade_postcondition$" in sql
    assert sql.count("-- phase3m append migration ") == 10
    assert "-- phase3m append migration 20260802140000" in sql
    assert "-- phase3m append migration 20260802147000" in sql
    assert "-- phase3m append migration 20260802148000" in sql
    assert "-- phase3m append migration 20260802149000" in sql
    assert "-- phase3m append migration 20260720143400" not in sql
    assert "UPDATE phase3m_internal.bootstrap_history" not in sql
    assert "DELETE FROM phase3m_internal.bootstrap_history" not in sql
    assert "TRUNCATE phase3m_internal.bootstrap_history" not in sql
    assert "DROP TABLE phase3m_internal.bootstrap_history" not in sql
    assert OLD_BOOTSTRAP_COMMIT in sql
    assert current_commit in sql
    assert applied.sha256 in sql
    assert candidate.sha256 in sql


def test_upgrade_history_rows_support_multiple_immutable_introduction_segments() -> None:
    module = _module()
    _applied, candidate = _manifests(module)
    current_commit = _current_commit()
    first_manifest = module.RUNNER.load_ancestor_manifest(
        module.DEFAULT_MANIFEST,
        ancestor_commit=OLD_BOOTSTRAP_COMMIT,
        expected_head_commit=current_commit,
    )
    second_segment = module.HistorySegment(
        start=12,
        end=19,
        commit=current_commit,
        manifest=candidate,
    )
    rows = module._history_rows(
        candidate_manifest=candidate,
        candidate_commit="f" * 40,
        applied_count=19,
        segments=(
            module.HistorySegment(
                start=1,
                end=11,
                commit=OLD_BOOTSTRAP_COMMIT,
                manifest=first_manifest,
            ),
            second_segment,
        ),
    )

    assert len(rows) == 21
    assert {row[-1] for row in rows[:11]} == {OLD_BOOTSTRAP_COMMIT}
    assert {row[-1] for row in rows[11:19]} == {current_commit}
    assert {row[-1] for row in rows[19:]} == {"f" * 40}


def test_upgrade_renderer_is_commit_bound_offline_and_confines_output() -> None:
    module = _module()
    source = SCRIPT.read_text(encoding="utf-8")

    assert "RUNNER._tested_commit(expected_commit)" in source
    assert "RUNNER._verified_git_bytes(Path(__file__), candidate_commit)" in source
    assert "RUNNER._verified_git_bytes(RUNNER_PATH, candidate_commit)" in source
    assert "RUNNER.load_ancestor_manifest" in source
    assert '"remote_connection_attempted": False' in source
    assert module._safe_output_path(
        ROOT / "reports" / "phase3m-upgrade-test.sql"
    ) == (ROOT / "reports" / "phase3m-upgrade-test.sql").resolve()
    for forbidden in ("psycopg", "supabase db push", "DATABASE_URL", "PGPASSWORD"):
        assert forbidden not in source


def test_upgrade_runtime_gate_is_local_network_isolated_and_fail_closed() -> None:
    source = RUNTIME_GATE.read_text(encoding="utf-8")

    assert "RUNNER._tested_commit(expected_commit)" in source
    assert "RUNNER._verified_git_bytes(source, candidate_commit)" in source
    assert '"--network",\n                "none"' in source
    assert "RUNNER.LOCAL_DOCKER_ENDPOINTS" in source
    assert "remote-docker-context-rejected" in source
    assert "phase3m-upgrade-history-mismatch" in source
    assert "phase3m_upgrade_check:suffix_contract" in source
    assert "suffix_contract_passed" in source
    assert "RUNNER._parse_contract_output" not in source
    assert '"external_credentials_used": False' in source
    assert '"external_migration_applied": False' in source
    for forbidden in ("psycopg", "DATABASE_URL", "PGPASSWORD", '"-p"', '"--publish"'):
        assert forbidden not in source
