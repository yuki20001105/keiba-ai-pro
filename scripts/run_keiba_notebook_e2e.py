#!/usr/bin/env python3
"""Run keibaAI notebook E2E pipeline (00 -> 08) via nbconvert.

Usage:
  python scripts/run_keiba_notebook_e2e.py --mode audit
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IGNORED_NOTEBOOK_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "reports",
    "test-results",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run notebooks/00_config.ipynb to 08_reporting.ipynb in sequence."
    )
    parser.add_argument(
        "--mode",
        choices=["audit", "prod"],
        default="audit",
        help="audit: AUDIT_MODE=1 FAST_MODE=1, prod: AUDIT_MODE=0 FAST_MODE=0",
    )
    return parser


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_notebooks(root: Path) -> list[Path]:
    notebooks: list[Path] = []
    for path in sorted(root.rglob("*.ipynb")):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_NOTEBOOK_PARTS for part in rel.parts):
            continue
        notebooks.append(rel)
    return notebooks


def mode_env(mode: str) -> dict[str, str]:
    if mode == "audit":
        return {"AUDIT_MODE": "1", "FAST_MODE": "1"}
    return {"AUDIT_MODE": "0", "FAST_MODE": "0"}


def resolve_jupyter_command() -> list[str]:
    project_root = repo_root()
    venv_python_candidates = [
        project_root / "python-api" / ".venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "Scripts" / "python.exe",
    ]
    for python_exe in venv_python_candidates:
        if python_exe.exists():
            return [str(python_exe), "-m", "jupyter"]

    jupyter = shutil.which("jupyter")
    if jupyter:
        return [jupyter]
    # Fallback for environments where jupyter is not on PATH.
    return [sys.executable, "-m", "jupyter"]


def ensure_targets(root: Path) -> tuple[Path, Path, Path]:
    notebooks_dir = root / "notebooks"
    reports_dir = root / "reports"
    out_dir = reports_dir / "e2e_notebooks"
    result_json = reports_dir / "keiba_notebook_e2e_result.json"

    reports_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    return notebooks_dir, out_dir, result_json


def run_single_notebook(
    jupyter_cmd: list[str],
    notebook_path: Path,
    output_dir: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started_ts = datetime.now(timezone.utc)

    cmd = [
        *jupyter_cmd,
        "nbconvert",
        "--to",
        "notebook",
        "--execute",
        str(notebook_path),
        "--output",
        notebook_path.name,
        "--output-dir",
        str(output_dir),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(notebook_path.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    ended_ts = datetime.now(timezone.utc)
    ended_at = utc_now_iso()
    elapsed_sec = round((ended_ts - started_ts).total_seconds(), 3)

    stderr_tail = "\n".join(proc.stderr.splitlines()[-20:]) if proc.stderr else ""
    stdout_tail = "\n".join(proc.stdout.splitlines()[-20:]) if proc.stdout else ""

    return {
        "notebook": notebook_path.name,
        "source_path": str(notebook_path),
        "executed_path": str(output_dir / notebook_path.name),
        "status": "success" if proc.returncode == 0 else "failed",
        "return_code": proc.returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": elapsed_sec,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    root = repo_root()
    notebooks_dir, output_dir, result_json = ensure_targets(root)
    jupyter_cmd = resolve_jupyter_command()

    started_at = utc_now_iso()
    started_ts = datetime.now(timezone.utc)

    result: dict[str, Any] = {
        "mode": args.mode,
        "started_at": started_at,
        "ended_at": None,
        "elapsed_seconds": None,
        "success": False,
        "failed_notebook": None,
        "executed_notebooks": [],
        "output_dir": str(output_dir),
        "result_json_path": str(result_json),
        "env": mode_env(args.mode),
    }

    notebook_files = discover_notebooks(root)

    if not notebook_files:
        raise FileNotFoundError(
            "No notebook files found under the repository root outside ignored directories"
        )

    print("Executed notebooks (planned):")
    for nb_path in notebook_files:
        print(f"- {nb_path.as_posix()}")

    env = os.environ.copy()
    env.update(mode_env(args.mode))
    env["PYTHONUTF8"] = "1"
    python_paths = [str(root / "keiba"), str(root / "python-api")]
    current_pythonpath = env.get("PYTHONPATH", "")
    if current_pythonpath:
        python_paths.append(current_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    try:
        for rel_nb_path in notebook_files:
            nb_path = root / rel_nb_path
            if not nb_path.exists():
                raise FileNotFoundError(f"Notebook not found: {nb_path}")

            print(f"\n[RUN] {rel_nb_path.as_posix()}")
            row = run_single_notebook(jupyter_cmd, nb_path, output_dir, env)
            result["executed_notebooks"].append(row)

            status = row["status"]
            elapsed = row["elapsed_seconds"]
            print(f"[RESULT] {rel_nb_path.as_posix()}: {status} ({elapsed}s)")

            if status != "success":
                result["failed_notebook"] = rel_nb_path.as_posix()
                break

    except Exception as e:  # noqa: BLE001
        result["failed_notebook"] = result.get("failed_notebook") or "runtime_error"
        result["error"] = str(e)

    ended_ts = datetime.now(timezone.utc)
    result["ended_at"] = utc_now_iso()
    result["elapsed_seconds"] = round((ended_ts - started_ts).total_seconds(), 3)

    failed = result.get("failed_notebook")
    result["success"] = failed is None

    write_result(result_json, result)

    print("\nSummary:")
    for row in result["executed_notebooks"]:
        print(
            f"- {row['notebook']}: {row['status']} ({row['elapsed_seconds']}s)"
        )

    if failed:
        print(f"failed notebook: {failed}")
    print(f"result json path: {result_json}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
