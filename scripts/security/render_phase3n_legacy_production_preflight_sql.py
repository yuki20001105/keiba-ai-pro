from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
ADOPTION_RENDERER_PATH = (
    ROOT / "scripts" / "security" / "render_phase3n_legacy_production_adoption_sql.py"
)
DEFAULT_CONTRACT = (
    ROOT
    / "reports"
    / "evidence"
    / "phase3n"
    / "phase3n_legacy_production_adoption_contract_20260816.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "generated" / "phase3n" / "phase3n_legacy_production_preflight.sql"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class RenderFailure(RuntimeError):
    pass


def _load_adoption_renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase3n_legacy_adoption_for_preflight",
        ADOPTION_RENDERER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RenderFailure("adoption-renderer-import-unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADOPTION = _load_adoption_renderer()


def build_read_only_preflight(
    *,
    contract: dict[str, object],
    contract_sha256: str,
) -> str:
    return "\n".join(
        (
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;",
            f"-- provider project ref (out-of-band binding) {contract['production_project_ref']}",
            f"-- phase3n adoption contract sha256 {contract_sha256}",
            "SET LOCAL statement_timeout = '300s';",
            "SET LOCAL idle_in_transaction_session_timeout = '300s';",
            ADOPTION._preflight_block(contract),
            (
                "SELECT jsonb_build_object("
                "'status', 'PASS', "
                "'production_project_ref', "
                f"'{contract['production_project_ref']}', "
                "'candidate_commit_sha', "
                f"'{contract['candidate_commit_sha']}', "
                "'contract_sha256', "
                f"'{contract_sha256}', "
                "'checked_at', clock_timestamp(), "
                "'transaction_read_only', current_setting('transaction_read_only')"
                ") AS phase3n_legacy_production_preflight;"
            ),
            "COMMIT;",
            "",
        )
    )


def render_preflight(
    *,
    contract_path: Path,
    expected_commit: str,
    output_path: Path,
) -> dict[str, object]:
    commit = expected_commit.strip().lower()
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise RenderFailure("candidate-commit-invalid")
    raw, contract_sha256 = ADOPTION._load_contract(contract_path)
    contract = ADOPTION._validated_contract(raw, expected_commit=commit)
    sql = build_read_only_preflight(
        contract=contract,
        contract_sha256=contract_sha256,
    )
    required = (
        "REPEATABLE READ, READ ONLY",
        "$phase3n_legacy_preflight$",
        "transaction_read_only",
        "COMMIT;",
    )
    if not all(fragment in sql for fragment in required):
        raise RenderFailure("preflight-contract-incomplete")
    upper = sql.upper()
    forbidden = (
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
    )
    if any(fragment in upper for fragment in forbidden):
        raise RenderFailure("preflight-not-read-only")

    output = ADOPTION._safe_report_path(output_path, suffix=".sql")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(sql, encoding="utf-8", newline="\n")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "candidate_commit_sha": commit,
        "production_project_ref": contract["production_project_ref"],
        "contract_sha256": contract_sha256,
        "output": output.relative_to(ROOT).as_posix(),
        "read_only": True,
        "remote_connection_attempted": False,
        "production_changed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the read-only, repeatable-read legacy Production preflight."
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_preflight(
            contract_path=args.contract,
            expected_commit=args.expected_commit,
            output_path=args.output,
        )
    except (RenderFailure, ADOPTION.RenderFailure) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
