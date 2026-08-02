from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import render_phase3m_supabase_upgrade_sql as upgrade


ROOT = Path(__file__).resolve().parents[2]
RUNNER = upgrade.RUNNER
OLD_BOOTSTRAP_COMMIT = "861f46c18b086578e97c15d6eaa12aed89222169"
CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


class UpgradeGateFailure(RuntimeError):
    pass


def _require(result: object, code: str) -> str:
    if result.returncode != 0:
        raise UpgradeGateFailure(code)
    return result.stdout.strip()


def _verify_history(
    container: str,
    database: str,
    expected_rows: tuple[tuple[object, ...], ...],
) -> None:
    output = _require(
        RUNNER._psql(
            container,
            database,
            """
SELECT concat_ws(
    '|', ordinal::TEXT, version, path, source, migration_sha256,
    chain_digest, bootstrap_id, manifest_sha256, expected_commit_sha
)
FROM phase3m_internal.bootstrap_history
ORDER BY ordinal;
""",
            timeout=30,
        ),
        "upgrade-history-read-failed",
    )
    expected = ["|".join(str(value) for value in row) for row in expected_rows]
    if output.splitlines() != expected:
        raise UpgradeGateFailure("upgrade-history-mismatch")


def run_gate(expected_commit: str) -> dict[str, object]:
    candidate_commit = RUNNER._tested_commit(expected_commit)
    for source in (Path(__file__), upgrade.Path(upgrade.__file__), upgrade.RUNNER_PATH):
        RUNNER._verified_git_bytes(source, candidate_commit)

    candidate = RUNNER.load_manifest(
        upgrade.DEFAULT_MANIFEST,
        expected_commit=candidate_commit,
    )
    applied = RUNNER.load_ancestor_manifest(
        upgrade.DEFAULT_MANIFEST,
        ancestor_commit=OLD_BOOTSTRAP_COMMIT,
        expected_head_commit=candidate_commit,
    )
    segments = upgrade._load_segments(
        [f"1:{len(applied.migrations)}:{OLD_BOOTSTRAP_COMMIT}"],
        candidate_manifest=candidate,
        expected_commit=candidate_commit,
        applied_count=len(applied.migrations),
        manifest_path=upgrade.DEFAULT_MANIFEST,
    )
    old_chain = RUNNER.build_chain_transaction(
        applied,
        expected_commit=OLD_BOOTSTRAP_COMMIT,
    )
    upgrade_sql = upgrade.build_upgrade_transaction(
        candidate_manifest=candidate,
        candidate_commit=candidate_commit,
        applied_count=len(applied.migrations),
        segments=segments,
    )
    expected_rows = upgrade._history_rows(
        candidate_manifest=candidate,
        candidate_commit=candidate_commit,
        applied_count=len(applied.migrations),
        segments=segments,
    )
    prelude_sql, _ = RUNNER._read_required_sql(
        RUNNER.PRELUDE,
        expected_commit=candidate_commit,
    )
    contract_sql, _ = RUNNER._read_required_sql(
        RUNNER.CONTRACT,
        expected_commit=candidate_commit,
    )

    container = f"keiba-phase3m-upgrade-{secrets.token_hex(8)}"
    database = f"phase3m_upgrade_{secrets.token_hex(6)}"
    password = secrets.token_urlsafe(32)
    workspace: Path | None = None
    local_context_confirmed = False
    docker_environment = ExitStack()
    checks = {
        "old_chain_applied": False,
        "append_only_upgrade_applied": False,
        "full_history_verified": False,
        "current_contract_passed": False,
        "replay_rejected": False,
        "cleanup_complete": False,
    }
    try:
        workspace = Path(tempfile.mkdtemp(prefix="phase3m-upgrade-"))
        docker_config = workspace / "docker-config"
        docker_home = workspace / "docker-home"
        docker_config.mkdir(mode=0o700)
        docker_home.mkdir(mode=0o700)
        (docker_config / "config.json").write_text("{}\n", encoding="utf-8")
        docker_environment.enter_context(
            patch.dict(
                os.environ,
                {"DOCKER_CONFIG": str(docker_config), "HOME": str(docker_home)},
                clear=False,
            )
        )
        endpoint = _require(
            RUNNER._docker(
                "context",
                "inspect",
                "--format",
                '{{(index .Endpoints "docker").Host}}',
                timeout=20,
            ),
            "docker-context-unavailable",
        )
        if not endpoint.startswith(RUNNER.LOCAL_DOCKER_ENDPOINTS):
            raise UpgradeGateFailure("remote-docker-context-rejected")
        local_context_confirmed = True
        _require(RUNNER._docker("pull", RUNNER.IMAGE, timeout=240), "docker-image-unavailable")
        started = _require(
            RUNNER._docker(
                "run",
                "--detach",
                "--name",
                container,
                "--network",
                "none",
                "--pull",
                "never",
                "--label",
                "keiba-ai-pro.phase3m-upgrade=true",
                "--env",
                f"POSTGRES_DB={database}",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                f"POSTGRES_PASSWORD={password}",
                RUNNER.IMAGE,
                timeout=40,
            ),
            "container-start-failed",
        )
        if CONTAINER_ID_PATTERN.fullmatch(started) is None:
            raise UpgradeGateFailure("container-id-invalid")
        network = _require(
            RUNNER._docker(
                "inspect",
                "--format",
                "{{.HostConfig.NetworkMode}}|{{json .NetworkSettings.Ports}}",
                container,
                timeout=20,
            ),
            "container-network-inspection-failed",
        )
        if network not in {"none|null", "none|{}"}:
            raise UpgradeGateFailure("container-network-not-isolated")
        RUNNER._wait_for_postgres(container, database)
        RUNNER._assert_fresh_database(container, database)
        _require(RUNNER._psql(container, database, prelude_sql), "supabase-prelude-failed")
        _require(
            RUNNER._psql(container, database, old_chain, timeout=300),
            "old-chain-apply-failed",
        )
        checks["old_chain_applied"] = True
        _require(
            RUNNER._psql(container, database, upgrade_sql, timeout=300),
            "append-only-upgrade-failed",
        )
        checks["append_only_upgrade_applied"] = True
        _verify_history(container, database, expected_rows)
        checks["full_history_verified"] = True
        contract_output = _require(
            RUNNER._psql(container, database, contract_sql, timeout=180),
            "current-contract-failed",
        )
        RUNNER._parse_contract_output(contract_output)
        checks["current_contract_passed"] = True
        replay = RUNNER._psql(container, database, upgrade_sql, timeout=300)
        checks["replay_rejected"] = (
            replay.returncode != 0
            and "phase3m-upgrade-history-mismatch" in replay.stderr
        )
        if not checks["replay_rejected"]:
            raise UpgradeGateFailure("upgrade-replay-not-rejected")
    finally:
        if local_context_confirmed:
            RUNNER._docker("rm", "--force", container, timeout=40)
        docker_environment.close()
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
        checks["cleanup_complete"] = (
            (not local_context_confirmed or RUNNER._container_absent(container))
            and (workspace is None or not workspace.exists())
        )

    if not all(checks.values()):
        raise UpgradeGateFailure("upgrade-check-incomplete")
    return {
        "success": True,
        "evidence_mode": "synthetic",
        "network_mode": "none",
        "tested_commit_sha": candidate_commit,
        "prior_migration_count": len(applied.migrations),
        "appended_migration_count": len(candidate.migrations) - len(applied.migrations),
        "final_migration_count": len(candidate.migrations),
        "checks": checks,
        "external_credentials_used": False,
        "external_migration_applied": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise the commit-bound Phase3M append-only upgrade in an isolated local PostgreSQL container."
    )
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        result = run_gate(args.expected_commit)
    except (UpgradeGateFailure, RUNNER.GateFailure, upgrade.RenderFailure) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
