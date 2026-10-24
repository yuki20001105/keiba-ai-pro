from __future__ import annotations

import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("local_release_readonly_smoke", ROOT / "scripts/smoke_local_release_readonly.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:3000/", "http://[::1]:8000"])
def test_loopback_origins_allowed(url):
    assert smoke.local_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "https://example.test", "http://0.0.0.0:8000", "http://192.168.1.4:8000",
    "http://localhost.example.test:8000", "http://token@127.0.0.1:8000",
    "http://127.0.0.1:8000/api/local-login", "http://127.0.0.1:8000?token=secret",
    "http://127.0.0.1:8000#secret", "http://127.0.0.1:0", "http://localhost:bad",
])
def test_remote_or_credential_origins_rejected(url):
    with pytest.raises(ValueError):
        smoke.local_base_url(url)


class Response(BytesIO):
    status = 200


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return Response(json.dumps(self.response).encode())


def test_health_sanitized_and_requests_have_no_credentials():
    opener = Opener({"status": "ok", "app_env": "development", "automated_betting_enabled": False,
                     "prediction_runtime": {"max_workers": 4, "active_workers": 0, "backend": "process"},
                     "token": "never-output-this", "owner": "private-owner"})
    result = smoke.http_probe(opener, "http://127.0.0.1:8000", "/health", health=True)
    assert result["passed"]
    assert result["health"]["prediction_runtime"]["max_workers"] == 4
    encoded = json.dumps(result)
    assert "never-output-this" not in encoded and "private-owner" not in encoded
    request = opener.requests[0]
    assert request.method == "GET" and request.data is None
    assert not request.has_header("Authorization") and not request.has_header("Cookie")


def test_invalid_health_values_do_not_crash_or_leak():
    opener = Opener({"status": "ok", "app_env": {"secret": "private"}, "prediction_runtime": {"backend": ["private"], "max_workers": True}})
    result = smoke.http_probe(opener, "http://127.0.0.1:8000", "/health", health=True)
    assert result["health"]["app_env"] == "unknown"
    assert result["health"]["prediction_runtime"]["backend"] == "unknown"
    assert result["health"]["prediction_runtime"]["max_workers"] is None
    assert "private" not in json.dumps(result)


def test_unauthenticated_performance_does_not_read_private_body():
    body = BytesIO(b'{"owner":"private", "tickets":"private"}')
    opener = Opener(error=HTTPError("http://127.0.0.1:3000/api/performance", 401, "Unauthorized", {}, body))
    result = smoke.http_probe(opener, "http://127.0.0.1:3000", "/api/performance")
    assert result["passed"] and result["status"] == 401
    assert "private" not in json.dumps(result)


def test_local_redirect_guard_not_followed():
    opener = Opener(error=HTTPError("http://127.0.0.1:3000/train", 307, "Redirect", {"Location": "/login"}, BytesIO()))
    assert smoke.http_probe(opener, "http://127.0.0.1:3000", "/train", page=True)["passed"]
    assert len(opener.requests) == 1
    assert smoke.NoRedirect().redirect_request(None, None, 307, None, {}, "https://example.test") is None


def test_remote_login_redirect_is_not_accepted():
    opener = Opener(error=HTTPError("http://127.0.0.1:3000/train", 307, "Redirect", {"Location": "https://example.test/login"}, BytesIO()))
    assert not smoke.http_probe(opener, "http://127.0.0.1:3000", "/train", page=True)["passed"]


@pytest.mark.parametrize("path", ["/api/local-login", "/api/ml/train/start", "/api/scrape", "/api/predict"])
def test_mutation_and_login_endpoints_not_probeable(path):
    opener = Opener()
    with pytest.raises(ValueError):
        smoke.http_probe(opener, "http://127.0.0.1:3000", path)
    assert opener.requests == []


def test_missing_database_not_created(tmp_path):
    target = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        with smoke.readonly_database(target):
            pass
    assert not target.exists()


def test_sqlite_enforced_readonly_and_aggregate_private_inventory(tmp_path):
    target = tmp_path / "ledger.db"
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE betting_performance_records(owner,payload_json,mode,status)")
        db.execute("INSERT INTO betting_performance_records VALUES('private-owner','private-payload','paper','pending')")
        db.execute("INSERT INTO betting_performance_records VALUES('private-owner','private-payload','actual','settled')")
    before = target.read_bytes()
    with smoke.readonly_database(target) as db:
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM betting_performance_records")
    result = smoke.database_inventory(tmp_path, "ledger.db", ("betting_performance_records",))
    assert result["available"] and result["counts"]["betting_performance_records"] == 2
    assert len(result["paper_actual_state_counts"]) == 2
    assert "private" not in json.dumps(result)
    assert target.read_bytes() == before


def test_unknown_job_state_not_leaked(tmp_path):
    with sqlite3.connect(tmp_path / "jobs.db") as db:
        db.execute("CREATE TABLE scrape_jobs(status,owner)")
        db.execute("INSERT INTO scrape_jobs VALUES('secret-status','private-owner')")
    result = smoke.database_inventory(tmp_path, "jobs.db", ("scrape_jobs",), group=("scrape_jobs", "status", smoke.JOB_STATES))
    assert result["state_counts"] == {"unknown": 1}
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)


def test_empty_schema_not_reported_ready(tmp_path):
    with sqlite3.connect(tmp_path / "jobs.db") as db:
        db.execute("CREATE TABLE other(value)")
    result = smoke.database_inventory(tmp_path, "jobs.db", ("scrape_jobs",))
    assert result["available"] and not result["schema_ready"]
    assert result["missing_tables"] == ["scrape_jobs"]


def test_scoped_path_rejects_traversal_and_external_symlinks(tmp_path):
    with pytest.raises(ValueError):
        smoke.scoped_path(tmp_path, "../outside.db")


def test_model_artifact_not_deserialized(tmp_path):
    directory = tmp_path / "python-api/models"
    directory.mkdir(parents=True)
    (directory / ".active_model.json").write_text('{"model_id":"model_fixture_20261010"}', encoding="utf-8")
    artifact = directory / "model_fixture_20261010.joblib"
    artifact.write_bytes(b"not executable or deserialized")
    before = artifact.read_bytes()
    result = smoke.model_inventory(tmp_path)
    assert result["available"] and result["active_artifact_exists"]
    assert result["deserialized"] is False and result["changed"] is False
    assert artifact.read_bytes() == before


def test_model_pointer_cannot_escape(tmp_path):
    directory = tmp_path / "python-api/models"
    directory.mkdir(parents=True)
    (directory / ".active_model.json").write_text('{"model_id":"../../private"}', encoding="utf-8")
    result = smoke.model_inventory(tmp_path)
    assert not result["available"]
    assert "private" not in json.dumps(result)
