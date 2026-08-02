from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.phase3n-ha.yml"
REPORTS = ROOT / "reports"


class HarnessFailure(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    check: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise HarnessFailure(
            f"command-failed:{command[0]}:{result.returncode}:"
            f"{(result.stderr or result.stdout)[-2000:]}"
        )
    return result


def _json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise HarnessFailure("json-output-missing")


def _wait_log(container: str, needle: str, *, env: dict[str, str], timeout: int = 60) -> str:
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        result = _run(["docker", "logs", container], env=env, check=False, timeout=10)
        output = (result.stdout or "") + (result.stderr or "")
        if needle in output:
            return output
        inspect = _run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            env=env,
            check=False,
            timeout=10,
        )
        if inspect.returncode != 0 or inspect.stdout.strip() == "exited":
            raise HarnessFailure(f"container-exited-before-event:{container}:{output[-1000:]}")
        time.sleep(0.5)
    raise HarnessFailure(f"container-log-timeout:{container}:{output[-1000:]}")


def _compose(project: str, env: dict[str, str], *args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        env=env,
        check=check,
        timeout=timeout,
    )


def _run_controller(project: str, env: dict[str, str], *args: str, timeout: int = 90) -> dict[str, Any]:
    result = _compose(project, env, "run", "--rm", "controller", *args, timeout=timeout)
    return _json_line(result.stdout)


def _start_hold(
    project: str,
    env: dict[str, str],
    *,
    service: str,
    container: str,
    instance: str,
) -> str:
    _compose(
        project,
        env,
        "run",
        "-d",
        "--name",
        container,
        service,
        "claim-hold",
        "--instance-id",
        instance,
        "--lease-seconds",
        "3",
        "--hold-seconds",
        "300",
    )
    return _wait_log(container, '"event": "claimed"', env=env)


def _scenario(
    project: str,
    env: dict[str, str],
    *,
    scenario: str,
    signal_name: str | None,
    network_partition: bool = False,
) -> list[str]:
    logs: list[str] = []
    _run_controller(project, env, "enqueue", "--scenario", scenario)
    instance_a = f"instance-a-{scenario}"
    instance_b = f"instance-b-{scenario}"
    container = f"phase3n-{project}-a-{scenario}"
    logs.append(
        _start_hold(
            project,
            env,
            service="instance-a",
            container=container,
            instance=instance_a,
        )
    )
    if network_partition:
        _run(["docker", "network", "disconnect", f"{project}_default", container], env=env)
    elif signal_name is not None:
        _run(["docker", "kill", "--signal", signal_name, container], env=env, check=False)
    takeover = _compose(
        project,
        env,
        "run",
        "--rm",
        "instance-b",
        "claim-apply",
        "--instance-id",
        instance_b,
        "--lease-seconds",
        "3",
        "--wait-seconds",
        "30",
        timeout=60,
    )
    logs.append(takeover.stdout)
    if network_partition:
        _run(["docker", "network", "connect", f"{project}_default", container], env=env)
    stale = _compose(
        project,
        env,
        "run",
        "--rm",
        "instance-a",
        "stale-apply",
        "--instance-id",
        instance_a,
        timeout=60,
    )
    logs.append(stale.stdout)
    _run(["docker", "kill", "--signal", "KILL", container], env=env, check=False)
    _run(["docker", "rm", "-f", container], env=env, check=False)
    return logs


def run_harness(output: Path) -> dict[str, Any]:
    if shutil.which("docker") is None:
        raise HarnessFailure("docker-cli-unavailable")
    project = f"phase3nha{uuid.uuid4().hex[:8]}"
    temporary = Path(tempfile.mkdtemp(prefix="phase3n-ha-evidence-"))
    temporary.chmod(0o777)
    env = dict(os.environ)
    env["PHASE3N_HA_EVIDENCE_DIR"] = str(temporary)
    timeline_parts: list[str] = []
    try:
        _compose(project, env, "up", "-d", "--build", "db", "rest", timeout=300)
        _run_controller(project, env, "wait-ready")
        _run_controller(project, env, "seed-cache-source")
        before = _run_controller(project, env, "digest")

        cache_container = f"phase3n-{project}-cache-crash"
        _compose(
            project,
            env,
            "run",
            "-d",
            "--name",
            cache_container,
            "controller",
            "cache-build",
            "--instance-id",
            "cache-instance-a",
            "--hold-seconds",
            "300",
        )
        cache_log = _wait_log(cache_container, '"event": "cache-built"', env=env)
        cache_before = _json_line(cache_log)
        _run(["docker", "kill", "--signal", "KILL", cache_container], env=env, check=False)
        _run(["docker", "rm", "-f", cache_container], env=env, check=False)
        _run_controller(project, env, "cache-delete")
        cache_after = _run_controller(project, env, "cache-build", "--instance-id", "cache-instance-b")
        cache_verification = _run_controller(project, env, "cache-verify")

        timeline_parts.extend(
            _scenario(project, env, scenario="sigkill", signal_name="KILL")
        )
        timeline_parts.extend(
            _scenario(project, env, scenario="sigterm", signal_name="TERM")
        )
        timeline_parts.extend(
            _scenario(project, env, scenario="network", signal_name=None, network_partition=True)
        )
        after = _run_controller(project, env, "digest")
        evidence_result = _compose(
            project, env, "run", "--rm", "controller", "evidence", timeout=60
        )
        evidence = _json_line(evidence_result.stdout)
        timeline_parts.append(evidence_result.stdout)
        report = {
            **evidence,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "compose_file": COMPOSE_FILE.name,
            "persistent_disk_used": False,
            "external_credentials_used": False,
            "production_connection_attempted": False,
            "render_billing_changed": False,
            "cache_before_sha256": cache_before["cache_digest_sha256"],
            "cache_after_sha256": cache_after["cache_digest_sha256"],
            "cache_digest_match": (
                cache_before["cache_digest_sha256"] == cache_after["cache_digest_sha256"]
            ),
            "cache_verification": cache_verification,
            "database_before_sha256": before["digest_sha256"],
            "database_after_sha256": after["digest_sha256"],
            "database_unchanged": before["digest_sha256"] == after["digest_sha256"],
        }
        report["success"] = bool(
            evidence.get("success")
            and report["cache_digest_match"]
            and cache_verification.get("success")
            and report["database_unchanged"]
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        timeline = output.with_name("phase3n_ha_timeline.jsonl")
        timeline.write_text("\n".join(timeline_parts), encoding="utf-8", newline="\n")
        return report
    except (HarnessFailure, OSError, subprocess.SubprocessError) as exc:
        compose_logs = _compose(
            project,
            env,
            "logs",
            "--no-color",
            "db-init",
            check=False,
            timeout=30,
        )
        raw_diagnostic = "\n".join(
            part for part in (str(exc), compose_logs.stdout, compose_logs.stderr) if part
        )
        diagnostic = raw_diagnostic.replace(str(ROOT), "<workspace>").replace(
            str(temporary), "<temporary-evidence>"
        )[-4000:]
        output.parent.mkdir(parents=True, exist_ok=True)
        failure_report = {
            "schema": "phase3n-ha-contract-evidence",
            "schema_version": 1,
            "success": False,
            "synthetic_contract_test": True,
            "production_evidence": False,
            "persistent_disk_used": False,
            "external_credentials_used": False,
            "production_connection_attempted": False,
            "render_billing_changed": False,
            "failure_code": "ha-contract-runtime-failed",
            "diagnostic_tail": diagnostic,
        }
        output.write_text(
            json.dumps(failure_report, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        output.with_name("phase3n_ha_timeline.jsonl").write_text(
            json.dumps(
                {"event": "harness-failed", "failure_code": "ha-contract-runtime-failed"},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        raise HarnessFailure(diagnostic) from exc
    finally:
        _compose(project, env, "down", "-v", "--remove-orphans", check=False, timeout=120)
        resolved = temporary.resolve()
        if resolved.parent == Path(tempfile.gettempdir()).resolve() and resolved.name.startswith(
            "phase3n-ha-evidence-"
        ):
            shutil.rmtree(resolved, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS / "phase3n_ha_contract_evidence.json",
    )
    args = parser.parse_args()
    output = args.output.resolve(strict=False)
    try:
        output.relative_to(REPORTS.resolve())
    except ValueError:
        print(json.dumps({"success": False, "failure_code": "output-outside-reports"}))
        return 1
    try:
        report = run_harness(output)
    except (HarnessFailure, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"success": False, "failure_code": str(exc)[-1000:]}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "success": report["success"],
                "output": output.relative_to(ROOT).as_posix(),
                "cache_digest_match": report["cache_digest_match"],
                "database_unchanged": report["database_unchanged"],
                "stale_fence_rejection_count": report["stale_fence_rejection_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
