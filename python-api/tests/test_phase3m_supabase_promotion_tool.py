from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "security" / "render_phase3m_supabase_bootstrap_sql.py"


def _module():
    spec = importlib.util.spec_from_file_location("phase3m_promotion_tool_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_output_is_confined_to_ignored_reports_directory(tmp_path: Path) -> None:
    module = _module()
    safe = ROOT / "reports" / "phase3m-test.sql"
    assert module._safe_output_path(safe) == safe.resolve()

    with pytest.raises(module.RenderFailure, match="output-must-be-under-reports"):
        module._safe_output_path(tmp_path / "unsafe.sql")
    with pytest.raises(module.RenderFailure, match="output-path-invalid"):
        module._safe_output_path(ROOT / "reports" / "not-sql.txt")


def test_renderer_is_commit_bound_and_has_no_remote_apply_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "_tested_commit(expected_commit)" in source
    assert "_verified_git_bytes(Path(__file__), commit)" in source
    assert "_verified_git_bytes(RUNNER_PATH, commit)" in source
    assert '"$phase3m_target_preflight$"' in source
    assert "TARGET_PREFLIGHT_REQUIRED_FRAGMENTS" in source
    assert "FENCING_SEQUENCE_PREFLIGHT_FRAGMENT" in source
    assert '"phase3m_internal.bootstrap_history"' in source
    assert "_require_canonical_manifest(manifest_path)" in source
    assert "expected_commit_sha" in source
    assert '"remote_connection_attempted": False' in source
    for forbidden in ("psycopg", "supabase db push", "DATABASE_URL", "PGPASSWORD"):
        assert forbidden not in source


def test_renderer_embeds_standalone_fencing_sequence_preflight() -> None:
    module = _module()
    fragment = module.RUNNER.FENCING_SEQUENCE_PREFLIGHT_FRAGMENT
    assert fragment in module.RUNNER.TARGET_PREFLIGHT_REQUIRED_FRAGMENTS

    manifest = module.RUNNER.load_manifest(module.DEFAULT_MANIFEST)
    sql = module.RUNNER.build_chain_transaction(manifest, expected_commit="a" * 40)
    assert "c.relkind = 'S'" in sql
    assert fragment in sql
    assert sql.index(fragment) < sql.index("phase3m migration")


def test_renderer_rejects_a_content_equivalent_alternate_manifest(tmp_path: Path) -> None:
    module = _module()
    alternate = tmp_path / "manifest.json"
    alternate.write_bytes(module.DEFAULT_MANIFEST.read_bytes())

    with patch.object(module.RUNNER, "_tested_commit") as tested_commit:
        with pytest.raises(module.RUNNER.GateFailure, match="manifest-path-not-canonical"):
            module.render_bundle(
                manifest_path=alternate,
                expected_commit="a" * 40,
                output_path=ROOT / "reports" / "must-not-exist.sql",
            )
    tested_commit.assert_not_called()
