from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "monitor_limited_production", ROOT / "scripts" / "monitor_limited_production.py"
)
assert SPEC and SPEC.loader
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


class Response:
    def __init__(self, status, payload):
        import json

        self.status = status
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return self.payload


def _env():
    return {
        "PRODUCTION_FRONTEND_HEALTH_URL": "https://frontend/api/health",
        "PRODUCTION_BACKEND_HEALTH_URL": "https://backend/health",
        "PRODUCTION_SUPABASE_URL": "https://project.supabase.co",
        "PRODUCTION_SUPABASE_SERVICE_KEY": "secret",
        "PRODUCTION_RENDER_SERVICE_ID": "srv-production",
        "RENDER_API_KEY": "render-secret",
    }


def _opener(request, timeout):
    url = request.full_url
    if url.endswith("/api/health") or url.endswith("/health"):
        return Response(200, {
            "status": "ok",
            "model_runtime_status": "observation",
            "observation_enabled": True,
            "observation_release_mode": "limited-observation",
            "automated_betting_enabled": False,
        })
    if "/auth/v1/health" in url:
        return Response(200, {"version": "test"})
    if "phase3n_operational_runtime_health" in url:
        return Response(200, [{"ready": True, "schema_version": 1}])
    if "phase3n_observation_ingest_attempts" in url:
        return Response(200, [])
    if "api.render.com" in url:
        return Response(200, [{"deploy": {"id": "dep-1", "status": "live", "commit": {"id": monitor.EXPECTED_SHA}}}])
    raise AssertionError(url)


def test_monitor_passes_only_with_all_fail_closed_boundaries():
    report = monitor.run(_env(), opener=_opener)
    assert report["success"] is True
    assert report["failure_codes"] == []
    assert len(report["probes"]) == 6


def test_monitor_fails_closed_when_configuration_is_missing():
    report = monitor.run({})
    assert report["success"] is False
    assert len(report["failure_codes"]) == 6
    assert report["probes"] == []


def test_runtime_probe_rejects_active_or_automated_betting():
    def unsafe(request, timeout):
        return Response(200, {
            "status": "ok",
            "model_runtime_status": "active",
            "observation_enabled": True,
            "observation_release_mode": "limited-observation",
            "automated_betting_enabled": True,
        })

    probe = monitor._runtime_probe("backend", "https://backend/health", opener=unsafe)
    assert probe.passed is False
    assert probe.code == "backend-safety-state-invalid"
    assert probe.facts["mismatched_fields"] == ["automated_betting_enabled", "model_runtime_status"]


def test_render_parser_accepts_wrapped_list_shape():
    assert monitor._find_render_deploy([
        {"deploy": {"id": "dep-1", "status": "live", "commit": {"id": monitor.EXPECTED_SHA}}}
    ]) == ("dep-1", monitor.EXPECTED_SHA, "live")
