from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "security" / "run_phase3m_supabase_bootstrap_gate.py"
DEFAULT_MANIFEST = ROOT / "supabase" / "bootstrap" / "v1" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3m_supabase_append_only_upgrade.sql"
SEGMENT_PATTERN = re.compile(r"^(?P<start>[1-9][0-9]*):(?P<end>[1-9][0-9]*):(?P<commit>[0-9a-fA-F]{40})$")


class RenderFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class HistorySegment:
    start: int
    end: int
    commit: str
    manifest: object


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase3m_bootstrap_runner_for_upgrade",
        RUNNER_PATH,
    )
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


def _migration_identity(entry: object) -> tuple[str, str, str, str]:
    return (entry.version, entry.path, entry.source, entry.sha256)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_segment(value: str) -> tuple[int, int, str]:
    matched = SEGMENT_PATTERN.fullmatch(value)
    if matched is None:
        raise RenderFailure("history-segment-invalid")
    start = int(matched.group("start"))
    end = int(matched.group("end"))
    if start > end:
        raise RenderFailure("history-segment-invalid")
    return start, end, matched.group("commit").lower()


def _load_segments(
    values: list[str],
    *,
    candidate_manifest: object,
    expected_commit: str,
    applied_count: int,
    manifest_path: Path,
) -> tuple[HistorySegment, ...]:
    parsed = [_parse_segment(value) for value in values]
    if not parsed or parsed[0][0] != 1 or parsed[-1][1] != applied_count:
        raise RenderFailure("history-segment-coverage-invalid")
    for previous, current in zip(parsed, parsed[1:]):
        if current[0] != previous[1] + 1:
            raise RenderFailure("history-segment-coverage-invalid")

    cache: dict[str, object] = {}
    segments: list[HistorySegment] = []
    for start, end, commit in parsed:
        manifest = cache.get(commit)
        if manifest is None:
            manifest = RUNNER.load_ancestor_manifest(
                manifest_path,
                ancestor_commit=commit,
                expected_head_commit=expected_commit,
            )
            cache[commit] = manifest
        if manifest.bootstrap_id != candidate_manifest.bootstrap_id or len(manifest.migrations) < end:
            raise RenderFailure("history-segment-manifest-invalid")
        historical = tuple(
            _migration_identity(entry)
            for entry in manifest.migrations[start - 1 : end]
        )
        candidate = tuple(
            _migration_identity(entry)
            for entry in candidate_manifest.migrations[start - 1 : end]
        )
        if historical != candidate:
            raise RenderFailure("history-segment-prefix-mismatch")
        segments.append(
            HistorySegment(start=start, end=end, commit=commit, manifest=manifest)
        )
    return tuple(segments)


def _history_row(
    ordinal: int,
    migration: object,
    manifest: object,
    commit: str,
) -> tuple[object, ...]:
    return (
        ordinal,
        migration.version,
        migration.path,
        migration.source,
        migration.sha256,
        manifest.chain_digest,
        manifest.bootstrap_id,
        manifest.sha256,
        commit,
    )


def _history_rows(
    *,
    candidate_manifest: object,
    candidate_commit: str,
    applied_count: int,
    segments: tuple[HistorySegment, ...],
) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for segment in segments:
        for ordinal in range(segment.start, segment.end + 1):
            rows.append(
                _history_row(
                    ordinal,
                    candidate_manifest.migrations[ordinal - 1],
                    segment.manifest,
                    segment.commit,
                )
            )
    for ordinal in range(applied_count + 1, len(candidate_manifest.migrations) + 1):
        rows.append(
            _history_row(
                ordinal,
                candidate_manifest.migrations[ordinal - 1],
                candidate_manifest,
                candidate_commit,
            )
        )
    return tuple(rows)


def _sql_values(rows: tuple[tuple[object, ...], ...]) -> str:
    return ",\n".join(
        "(" + ", ".join(
            str(value) if isinstance(value, int) else _sql_literal(value)
            for value in row
        ) + ")"
        for row in rows
    )


def _history_assertion(rows: tuple[tuple[object, ...], ...], *, phase: str) -> str:
    values = _sql_values(rows)
    return f"""
DO $phase3m_upgrade_{phase}$
BEGIN
    IF to_regnamespace('phase3m_internal') IS NULL
       OR to_regclass('phase3m_internal.bootstrap_history') IS NULL THEN
        RAISE EXCEPTION 'phase3m-upgrade-history-unavailable';
    END IF;
    IF EXISTS (
        WITH expected (
            ordinal, version, path, source, migration_sha256,
            chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
        ) AS (
            VALUES
{values}
        ),
        actual AS (
            SELECT ordinal, version, path, source, migration_sha256,
                   chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
            FROM phase3m_internal.bootstrap_history
        )
        SELECT 1
        FROM expected
        FULL OUTER JOIN actual USING (ordinal)
        WHERE expected.ordinal IS NULL
           OR actual.ordinal IS NULL
           OR expected.version IS DISTINCT FROM actual.version
           OR expected.path IS DISTINCT FROM actual.path
           OR expected.source IS DISTINCT FROM actual.source
           OR expected.migration_sha256 IS DISTINCT FROM actual.migration_sha256
           OR expected.chain_digest IS DISTINCT FROM actual.chain_digest
           OR expected.bootstrap_id IS DISTINCT FROM actual.bootstrap_id
           OR expected.manifest_sha256 IS DISTINCT FROM actual.manifest_sha256
           OR expected.expected_commit_sha IS DISTINCT FROM actual.expected_commit_sha
    ) THEN
        RAISE EXCEPTION 'phase3m-upgrade-history-mismatch';
    END IF;
END
$phase3m_upgrade_{phase}$;
""".strip()


def build_upgrade_transaction(
    *,
    candidate_manifest: object,
    candidate_commit: str,
    applied_count: int,
    segments: tuple[HistorySegment, ...],
) -> str:
    prior_rows = _history_rows(
        candidate_manifest=candidate_manifest,
        candidate_commit=candidate_commit,
        applied_count=applied_count,
        segments=segments,
    )[:applied_count]
    all_rows = _history_rows(
        candidate_manifest=candidate_manifest,
        candidate_commit=candidate_commit,
        applied_count=applied_count,
        segments=segments,
    )
    suffix_rows = all_rows[applied_count:]
    statements = [
        "BEGIN;",
        f"-- phase3m append-only upgrade candidate {candidate_commit}",
        f"-- phase3m append-only prior migration count {applied_count}",
        "SET LOCAL lock_timeout = '5s';",
        "SET LOCAL statement_timeout = '180s';",
        "SET LOCAL idle_in_transaction_session_timeout = '180s';",
        (
            "SELECT pg_catalog.pg_advisory_xact_lock("
            f"pg_catalog.hashtextextended('phase3m-upgrade:{candidate_manifest.bootstrap_id}:"
            f"{candidate_manifest.chain_digest}', 0));"
        ),
        _history_assertion(prior_rows, phase="preflight"),
    ]
    for migration in candidate_manifest.migrations[applied_count:]:
        try:
            sql = migration.content.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RenderFailure("migration-read-failed") from exc
        statements.extend(
            (
                f"-- phase3m append migration {migration.version} ({migration.path})",
                sql.rstrip(),
            )
        )
    statements.append(
        """
INSERT INTO phase3m_internal.bootstrap_history (
    ordinal, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
VALUES
""".strip()
        + "\n"
        + _sql_values(suffix_rows)
        + ";"
    )
    statements.extend((_history_assertion(all_rows, phase="postcondition"), "COMMIT;"))
    return "\n".join(statements) + "\n"


def render_upgrade_bundle(
    *,
    manifest_path: Path,
    expected_commit: str,
    applied_manifest_commit: str,
    history_segment_values: list[str],
    output_path: Path,
) -> dict[str, object]:
    canonical_manifest = RUNNER._require_canonical_manifest(manifest_path)
    candidate_commit = RUNNER._tested_commit(expected_commit)
    RUNNER._verified_git_bytes(Path(__file__), candidate_commit)
    RUNNER._verified_git_bytes(RUNNER_PATH, candidate_commit)
    candidate = RUNNER.load_manifest(
        canonical_manifest,
        expected_commit=candidate_commit,
    )
    applied = RUNNER.load_ancestor_manifest(
        canonical_manifest,
        ancestor_commit=applied_manifest_commit,
        expected_head_commit=candidate_commit,
    )
    if applied.bootstrap_id != candidate.bootstrap_id:
        raise RenderFailure("applied-manifest-bootstrap-id-mismatch")
    applied_count = len(applied.migrations)
    if applied_count >= len(candidate.migrations):
        raise RenderFailure("candidate-manifest-not-extended")
    if tuple(_migration_identity(entry) for entry in applied.migrations) != tuple(
        _migration_identity(entry) for entry in candidate.migrations[:applied_count]
    ):
        raise RenderFailure("candidate-manifest-prefix-mismatch")

    segments = _load_segments(
        history_segment_values,
        candidate_manifest=candidate,
        expected_commit=candidate_commit,
        applied_count=applied_count,
        manifest_path=canonical_manifest,
    )
    sql = build_upgrade_transaction(
        candidate_manifest=candidate,
        candidate_commit=candidate_commit,
        applied_count=applied_count,
        segments=segments,
    )
    required = (
        "BEGIN;",
        "$phase3m_upgrade_preflight$",
        "$phase3m_upgrade_postcondition$",
        "INSERT INTO phase3m_internal.bootstrap_history",
        "expected_commit_sha",
        "COMMIT;",
    )
    if not all(fragment in sql for fragment in required):
        raise RenderFailure("rendered-upgrade-contract-incomplete")
    if "postgresql://" in sql.lower() or "service_role_key" in sql.lower():
        raise RenderFailure("rendered-upgrade-secret-like-content")

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
        "candidate_commit_sha": candidate_commit,
        "applied_manifest_commit_sha": applied_manifest_commit.lower(),
        "bootstrap_id": candidate.bootstrap_id,
        "manifest_sha256": candidate.sha256,
        "chain_digest": candidate.chain_digest,
        "prior_migration_count": applied_count,
        "appended_migration_count": len(candidate.migrations) - applied_count,
        "final_migration_count": len(candidate.migrations),
        "history_segments": [
            {"start": segment.start, "end": segment.end, "commit": segment.commit}
            for segment in segments
        ],
        "output": output.relative_to(ROOT).as_posix(),
        "remote_connection_attempted": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a commit-bound append-only Phase 3M upgrade for an existing isolated Staging database."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--applied-manifest-commit", required=True)
    parser.add_argument(
        "--history-segment",
        action="append",
        required=True,
        help="Existing immutable history segment as START:END:INTRODUCTION_COMMIT; repeat in order.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_upgrade_bundle(
            manifest_path=args.manifest,
            expected_commit=args.expected_commit,
            applied_manifest_commit=args.applied_manifest_commit,
            history_segment_values=args.history_segment,
            output_path=args.output,
        )
    except (RenderFailure, RUNNER.GateFailure) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
