from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"
DEFAULT_MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3m_supabase_bootstrap_apply.sql"


class RenderFailure(RuntimeError):
    pass


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase3m_bootstrap_runner_for_render", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RenderFailure("runner-import-unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def _safe_output_path(path: Path) -> Path:
    reports = (ROOT / "reports").resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(reports)
    except ValueError as exc:
        raise RenderFailure("output-must-be-under-reports") from exc
    if resolved.suffix.lower() != ".sql" or resolved.exists() and resolved.is_symlink():
        raise RenderFailure("output-path-invalid")
    return resolved


def render_bundle(*, manifest_path: Path, expected_commit: str, output_path: Path) -> dict[str, object]:
    runner = RUNNER
    canonical_manifest = runner._require_canonical_manifest(manifest_path)
    commit = runner._tested_commit(expected_commit)

    # The executable renderer and runner are part of the trust boundary, not
    # just the migration inputs. Refuse working-tree substitutions.
    runner._verified_git_bytes(Path(__file__), commit)
    runner._verified_git_bytes(RUNNER_PATH, commit)

    manifest = runner.load_manifest(canonical_manifest, expected_commit=commit)
    sql = runner.build_chain_transaction(manifest, expected_commit=commit)
    required_fragments = (
        "BEGIN;",
        "$phase3m_target_preflight$",
        "phase3m_internal.bootstrap_history",
        f"-- phase3m expected commit {commit}",
        "expected_commit_sha",
        "COMMIT;",
        runner.FENCING_SEQUENCE_PREFLIGHT_FRAGMENT,
        *runner.TARGET_PREFLIGHT_REQUIRED_FRAGMENTS,
    )
    if not all(fragment in sql for fragment in required_fragments):
        raise RenderFailure("rendered-chain-contract-incomplete")
    if "postgresql://" in sql.lower() or "service_role_key" in sql.lower():
        raise RenderFailure("rendered-chain-secret-like-content")

    output = _safe_output_path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(sql, encoding="utf-8", newline="\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "schema_version": 1,
        "tested_commit_sha": commit,
        "bootstrap_id": manifest.bootstrap_id,
        "manifest_sha256": manifest.sha256,
        "chain_digest": manifest.chain_digest,
        "migration_count": len(manifest.migrations),
        "output": output.relative_to(ROOT).as_posix(),
        "remote_connection_attempted": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the commit-bound Phase 3M transaction for manual isolated-Staging apply."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_bundle(
            manifest_path=args.manifest,
            expected_commit=args.expected_commit,
            output_path=args.output,
        )
    except (RenderFailure, RUNNER.GateFailure) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
