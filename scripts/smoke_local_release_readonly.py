"""Read-only local release evidence; never scrape, train, predict, or place bets.

This is an unauthenticated boundary/health check plus aggregate SQLite inventory,
not proof that an authenticated five-feature workflow has completed. No runtime
modules, dotenv files, cookies, tokens, redirects, or model deserialization are
used. SQLite is opened mode=ro/query_only; performance's settling GET is not
called with authentication. Optional evidence output is a NEW file only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
API_GUARDS = (
    ("data_collection", "/api/scrape/health"),
    ("model_creation", "/api/ml/train/capability"),
    ("prediction", "/api/races/by-date?date=20261010"),
    ("performance", "/api/performance"),
    ("user_management", "/api/admin/profiles"),
)
PAGES = ("/data-collection", "/train", "/predict-batch", "/dashboard", "/user-management")
JOB_STATES = frozenset({
    "preparing", "queued", "claimed", "running", "recovering", "waiting_resources",
    "paused_resource", "artifact-registered", "completed", "failed", "error", "cancelled",
})
RECORD_STATES = frozenset({"pending", "settled", "skipped"})
SAFE_ID = re.compile(r"model_[A-Za-z0-9_-]{1,180}\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
        host = parsed.hostname or ""
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        if parsed.hostname != "localhost":
            raise ValueError("Only an HTTP loopback origin is allowed") from None
        loopback, port = True, parsed.port
    if (parsed.scheme != "http" or not loopback or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or port == 0):
        raise ValueError("Only an HTTP loopback origin is allowed")
    return value.rstrip("/")


def _bounded_int(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 64 else None


def _enum(value: Any, accepted: set[str]) -> str:
    return value if isinstance(value, str) and value in accepted else "unknown"


def _health_summary(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"valid_json": False}
    runtime = body.get("prediction_runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    return {
        "valid_json": True,
        "status_ok": body.get("status") == "ok",
        "app_env": _enum(body.get("app_env"), {"development", "local", "test", "staging", "production"}),
        "automated_betting_enabled": body.get("automated_betting_enabled") if type(body.get("automated_betting_enabled")) is bool else None,
        "prediction_runtime": {
            "backend": _enum(runtime.get("backend"), {"process", "thread", "serial"}),
            "max_workers": _bounded_int(runtime.get("max_workers")),
            "active_workers": _bounded_int(runtime.get("active_workers")),
        },
    }


def http_probe(opener, base: str, path: str, *, health: bool = False, page: bool = False) -> dict[str, Any]:
    # The call sites exclusively provide these fixed safe GET paths. Reject
    # arbitrary caller-supplied URLs, including the local autologin endpoint.
    allowed = {"/health", "/api/health", *(p for _, p in API_GUARDS), *PAGES}
    if path not in allowed:
        raise ValueError("Probe is not in the read-only allowlist")
    base = local_base_url(base)
    request = Request(base + path, method="GET", headers={"Accept": "application/json"})
    started = time.monotonic()
    result: dict[str, Any] = {"method": "GET", "path": path}
    try:
        with opener.open(request, timeout=8) as response:
            result["status"] = response.status
            if health:
                body = response.read(65537)
                result["health"] = _health_summary(json.loads(body)) if len(body) <= 65536 else {"valid_json": False}
    except HTTPError as exc:
        result["status"] = exc.code
        if page:
            location = urlsplit(exc.headers.get("Location", ""))
            result["login_redirect"] = (
                location.path == "/login"
                and (not location.netloc or f"{location.scheme}://{location.netloc}" == base)
            )
        exc.close()
    except (URLError, OSError, ValueError, TimeoutError) as exc:
        # Exception text or raw bodies might contain cookies, provider URLs,
        # credentials, or private payloads. Keep only a type-level diagnostic.
        result["error_type"] = type(exc).__name__
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    if health:
        result["passed"] = result.get("status") == 200 and result.get("health", {}).get("status_ok") is True
    elif page:
        result["passed"] = result.get("status") in (302, 303, 307, 308) and result.get("login_redirect") is True
    else:
        result["passed"] = result.get("status") in (401, 403)
    return result


def scoped_path(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Inventory path leaves the repository")
    return path


@contextmanager
def readonly_database(path: Path):
    if not path.is_file() or not path.stat().st_size:
        raise FileNotFoundError("Database absent or empty")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        connection.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 10
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        yield connection
    finally:
        connection.close()


def database_inventory(root: Path, relative: str, tables: tuple[str, ...], *, group: tuple[str, str, frozenset[str]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": relative, "read_only": True}
    try:
        with readonly_database(scoped_path(root, relative)) as db:
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["counts"] = {
                table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in tables if table in present
            }
            result["missing_tables"] = [table for table in tables if table not in present]
            result["schema_ready"] = not result["missing_tables"]
            if group and group[0] in present:
                table, column, accepted = group
                groups: dict[str, int] = {}
                for state, count in db.execute(f'SELECT "{column}", COUNT(*) FROM "{table}" GROUP BY "{column}"'):
                    label = state if state in accepted else "unknown"
                    groups[label] = groups.get(label, 0) + count
                result["state_counts"] = groups
            if "betting_performance_records" in present:
                result["paper_actual_state_counts"] = [
                    {"mode": mode if mode in {"paper", "actual"} else "unknown",
                     "status": state if state in RECORD_STATES else "unknown", "count": count}
                    for mode, state, count in db.execute("SELECT mode,status,COUNT(*) FROM betting_performance_records GROUP BY mode,status")
                ]
            result["available"] = True
    except (FileNotFoundError, ValueError, sqlite3.Error, OSError) as exc:
        result.update(available=False, error_type=type(exc).__name__)
    return result


def model_inventory(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"deserialized": False, "changed": False}
    try:
        directory = scoped_path(root, "python-api/models")
        result["joblib_count"] = sum(1 for p in directory.glob("model_*.joblib") if p.is_file())
        pointer = scoped_path(root, "python-api/models/.active_model.json")
        if pointer.stat().st_size > 4096:
            raise ValueError("Invalid active model pointer")
        model_id = json.loads(pointer.read_text(encoding="utf-8")).get("model_id")
        if not isinstance(model_id, str) or not SAFE_ID.fullmatch(model_id):
            raise ValueError("Invalid active model identifier")
        artifact = scoped_path(root, f"python-api/models/{model_id}.joblib")
        result.update(active_model_id=model_id, active_artifact_exists=artifact.is_file())
        if artifact.is_file():
            result["active_artifact_bytes"] = artifact.stat().st_size
            with artifact.open("rb") as stream:
                result["active_artifact_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        result["available"] = True
    except (OSError, ValueError, AttributeError) as exc:
        result.update(available=False, error_type=type(exc).__name__)
    return result


def collect_report(root: Path, api_url: str, web_url: str) -> dict[str, Any]:
    api_url, web_url = local_base_url(api_url), local_base_url(web_url)
    # Explicitly disable environment proxies and all redirect following.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    health = [http_probe(opener, api_url, "/health", health=True), http_probe(opener, web_url, "/api/health", health=True)]
    guards = [{"feature": feature, **http_probe(opener, web_url, path)} for feature, path in API_GUARDS]
    pages = [http_probe(opener, web_url, path, page=True) for path in PAGES]
    inventories = [
        database_inventory(root, "keiba/data/keiba_ultimate.db", ("races_ultimate", "race_results_ultimate", "prediction_log", "training_data", "speed_figures")),
        database_inventory(root, "keiba/data/scrape_jobs.db", ("scrape_jobs",), group=("scrape_jobs", "status", JOB_STATES)),
        database_inventory(root, "keiba/data/train_jobs.db", ("train_jobs",), group=("train_jobs", "status", JOB_STATES)),
        database_inventory(root, "python-api/data/local_retrain_jobs.db", ("local_retrain_jobs",), group=("local_retrain_jobs", "state", JOB_STATES)),
        database_inventory(root, "keiba/data/betting_performance.db", ("betting_performance_snapshots", "betting_performance_records")),
    ]
    safe_health = all(h.get("health", {}).get("automated_betting_enabled") is False and h.get("health", {}).get("app_env") in {"development", "local", "test"} for h in health)
    return {
        "version": 1, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "loopback_unauthenticated_boundaries_and_readonly_inventory",
        "health": health, "unauthenticated_api_guards": guards, "unauthenticated_page_guards": pages,
        "databases": inventories, "models": model_inventory(root),
        "boundary_smoke_pass": all(p["passed"] for p in health + guards + pages) and safe_health,
        "authenticated_real_workflow_verified": False,
        "release_complete": False,
        "no_mutation_requests_sent": True,
        "fixture_tests_do_not_prove_authenticated_real_workflow": True,
        "limitations": [
            "No authenticated scrape, train, predict, settlement, purchase, or role-change operation was performed.",
            "Inventory is an aggregate observation, not proof that persisted jobs are actively progressing.",
            "No account credentials, owner identities, private recommendation payloads, or dotenv contents were read.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path, help="New JSON evidence file under reports/generated/local-release-smoke")
    args = parser.parse_args()
    try:
        report = collect_report(args.repo_root, args.api_url, args.web_url)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            allowed = scoped_path(args.repo_root, "reports/generated/local-release-smoke")
            target = args.output.resolve()
            if not target.is_relative_to(allowed) or target.suffix != ".json":
                raise ValueError("Evidence output must be a new local-release-smoke JSON file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        print(encoded, end="")
        return 0 if report["boundary_smoke_pass"] else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"boundary_smoke_pass": False, "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
