from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_API = ROOT / "python-api"
if str(PYTHON_API) not in sys.path:
    sys.path.insert(0, str(PYTHON_API))

from observation.cache_integrity import (  # noqa: E402
    exercise_cache_rebuild,
    verify_cache,
    write_cache_atomic,
)
from observation.contracts import ObservationContractError  # noqa: E402
from observation.export import build_model_acceptance_source, write_gzip_atomic  # noqa: E402
from observation.progress import build_progress_report, render_progress_markdown  # noqa: E402
from observation.reconcile import reconcile_available_results  # noqa: E402
from observation.service import (  # noqa: E402
    ObservationConfig,
    ObservationGateway,
    join_progress_rows,
)


REPORTS = (ROOT / "reports").resolve()


def _safe_report_path(value: Path, suffix: str) -> Path:
    resolved = (value if value.is_absolute() else ROOT / value).resolve(strict=False)
    try:
        resolved.relative_to(REPORTS)
    except ValueError as exc:
        raise ObservationContractError("output-must-be-under-reports") from exc
    if resolved.suffix.lower() != suffix or resolved.is_symlink():
        raise ObservationContractError("output-path-invalid")
    return resolved


def _gateway() -> ObservationGateway:
    config = ObservationConfig.from_env()
    config.require_staging_boundary()
    from app_config import get_supabase_client  # type: ignore

    return ObservationGateway(get_supabase_client())


def _write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _progress(gateway: ObservationGateway) -> tuple[list[dict], list[dict], dict]:
    predictions, results, attempts = join_progress_rows(gateway)
    return predictions, results, build_progress_report(predictions, results, attempts)


def command_progress(args: argparse.Namespace) -> dict:
    gateway = _gateway()
    predictions, results, report = _progress(gateway)
    json_path = _safe_report_path(args.json_output, ".json")
    markdown_path = _safe_report_path(args.markdown_output, ".md")
    _write_text_atomic(
        json_path,
        json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n",
    )
    _write_text_atomic(markdown_path, render_progress_markdown(report))
    return {
        "success": True,
        "prediction_count": len(predictions),
        "result_count": len(results),
        "readiness_verdict": report["readiness_verdict"],
        "json": json_path.relative_to(ROOT).as_posix(),
        "markdown": markdown_path.relative_to(ROOT).as_posix(),
    }


def command_reconcile(_args: argparse.Namespace) -> dict:
    return {"success": True, **reconcile_available_results(_gateway())}


def command_cache_rebuild(args: argparse.Namespace) -> dict:
    gateway = _gateway()
    predictions, _results, _attempts = join_progress_rows(gateway)
    cache_path = _safe_report_path(args.cache_output, ".json")
    digest = write_cache_atomic(cache_path, predictions)
    verification = verify_cache(cache_path, predictions)
    return {
        "success": verification["success"],
        "cache": cache_path.relative_to(ROOT).as_posix(),
        "cache_digest_sha256": digest,
        **verification,
    }


def command_cache_integrity(args: argparse.Namespace) -> dict:
    gateway = _gateway()
    cache_path = _safe_report_path(args.cache_output, ".json")
    evidence_path = _safe_report_path(args.evidence_output, ".json")

    def load_rows() -> list[dict]:
        predictions, _results, _attempts = join_progress_rows(gateway)
        return predictions

    evidence = exercise_cache_rebuild(cache_path, load_rows)
    config = ObservationConfig.from_env()
    report = {
        **evidence,
        "candidate_commit_sha": config.candidate_commit_sha,
        "source_environment": "staging",
        "production_changed": False,
        "cache": cache_path.relative_to(ROOT).as_posix(),
    }
    _write_text_atomic(
        evidence_path,
        json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n",
    )
    return {
        "success": report["success"],
        "evidence": evidence_path.relative_to(ROOT).as_posix(),
        "candidate_commit_sha": config.candidate_commit_sha,
        "source_row_count": report["source_row_count"],
        "cache_digest_sha256": report["cache_digest_after_sha256"],
        "database_unchanged": report["database_unchanged"],
        "rebuild_ms": report["rebuild_ms"],
    }


def command_export(args: argparse.Namespace) -> dict:
    gateway = _gateway()
    predictions, results, report = _progress(gateway)
    payload = build_model_acceptance_source(
        predictions,
        results,
        report,
        initial_bankroll=args.initial_bankroll,
    )
    output = _safe_report_path(args.output, ".gz")
    return {"success": True, **write_gzip_atomic(output, payload)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Operate the Staging-only Phase 3N append-only observation ledger."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    progress = subparsers.add_parser("progress")
    progress.add_argument(
        "--json-output", type=Path, default=Path("reports/generated/phase3n/phase3n_observation_progress.json")
    )
    progress.add_argument(
        "--markdown-output", type=Path, default=Path("reports/generated/phase3n/phase3n_observation_progress.md")
    )
    progress.set_defaults(func=command_progress)
    reconcile = subparsers.add_parser("reconcile-results")
    reconcile.set_defaults(func=command_reconcile)
    cache = subparsers.add_parser("cache-rebuild")
    cache.add_argument(
        "--cache-output", type=Path, default=Path("reports/generated/phase3n/phase3n_observation_cache.json")
    )
    cache.set_defaults(func=command_cache_rebuild)
    integrity = subparsers.add_parser("cache-integrity")
    integrity.add_argument(
        "--cache-output", type=Path, default=Path("reports/generated/phase3n/phase3n_observation_cache.json")
    )
    integrity.add_argument(
        "--evidence-output",
        type=Path,
        default=Path("reports/generated/phase3n/phase3n_cache_integrity_evidence.json"),
    )
    integrity.set_defaults(func=command_cache_integrity)
    export = subparsers.add_parser("export-model-source")
    export.add_argument("--initial-bankroll", required=True, type=float)
    export.add_argument(
        "--output", type=Path, default=Path("reports/generated/phase3n/model_evaluation_observations.json.gz")
    )
    export.set_defaults(func=command_export)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = args.func(args)
    except ObservationContractError as exc:
        print(json.dumps({"success": False, "failure_code": exc.code}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return 0 if result.get("success") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
