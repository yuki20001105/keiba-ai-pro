from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECURITY_DIR = ROOT / "scripts" / "security"
if str(SECURITY_DIR) not in sys.path:
    sys.path.insert(0, str(SECURITY_DIR))

from phase2_authz_runtime import (  # type: ignore
    _detect_auth_header_read,
    _detect_direct_policy,
    _extract_proxy_targets,
    classify_fastapi_route,
    classify_fastapi_route_with_runtime,
    extract_next_routes,
    gate_next_routes,
    load_next_policy_canonical,
)


def test_unguarded_api_fixture_is_unclassified() -> None:
    policy = classify_fastapi_route("/api/fixtures/no-guard", dependency_names=[])
    assert policy == "Unclassified"


def test_fixture_without_jwt_middleware_keeps_api_route_unclassified() -> None:
    policy = classify_fastapi_route_with_runtime(
        "/api/races/by-date",
        dependency_names=[],
        jwt_middleware_targets_api=False,
        exempt_paths={"/", "/health"},
    )
    assert policy == "Unclassified"


def test_fixture_with_jwt_middleware_marks_api_route_authenticated() -> None:
    policy = classify_fastapi_route_with_runtime(
        "/api/races/by-date",
        dependency_names=[],
        jwt_middleware_targets_api=True,
        exempt_paths={"/", "/health"},
    )
    assert policy == "Authenticated"


def test_next_canonical_gate_detects_removed_and_unexpected_routes() -> None:
    extracted = extract_next_routes()
    canonical = load_next_policy_canonical()

    # Remove one canonical entry and add one unknown to emulate drift.
    mutated = list(canonical[1:])
    mutated.append(
        {
            "route": "/api/non-existent-fixture",
            "method": "GET",
            "policy": "Proxy",
            "auth_header_forward": False,
            "status_propagation": False,
            "detail_error_passthrough": False,
        }
    )

    failures = gate_next_routes(extracted, mutated)
    kinds = {f.kind for f in failures}
    assert "unexpected_route" in kinds or "missing_route" in kinds


def test_req_alias_authorization_detection() -> None:
    ts = """
export async function GET(req: NextRequest) {
  const auth = req.headers.get('Authorization') || ''
  return Response.json({ ok: !!auth })
}
"""
    assert _detect_auth_header_read(ts) is True


def test_direct_route_without_auth_is_unclassified() -> None:
    ts = """
export async function POST(request: NextRequest) {
  const body = await request.json()
  return Response.json({ ok: true, body })
}
"""
    assert _detect_direct_policy(ts, "/api/direct-no-auth") == "Unclassified"


def test_wrong_public_classification_is_detectable() -> None:
    extracted = [
        type("_R", (), {
            "route": "/api/ocr",
            "method": "POST",
            "policy": "Unclassified",
            "backend_endpoint": "",
            "backend_method": "",
            "auth_header_forward": False,
            "status_propagation": False,
            "detail_error_passthrough": False,
            "source": "src/app/api/ocr/route.ts",
        })()
    ]
    canonical = [
        {
            "route": "/api/ocr",
            "method": "POST",
            "policy": "Public",
            "auth_header_forward": False,
            "status_propagation": False,
            "detail_error_passthrough": False,
            "auth_header_forward_reason": "ocr route does not proxy bearer token to backend",
            "status_propagation_reason": "ocr returns normalized app error payload",
            "detail_error_passthrough_reason": "ocr route intentionally maps detail into top-level error",
        }
    ]
    failures = gate_next_routes(extracted, canonical)
    assert any(f.kind in {"mismatch", "unclassified_extracted"} for f in failures)


def test_proxy_target_parsing_with_template_query() -> None:
    ts = """
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const query = searchParams.toString()
  const url = `${ML_API_URL}/api/data_stats${query ? `?${query}` : ''}`
  const response = await fetch(url, { method: 'GET' })
  return NextResponse.json(await response.json(), { status: response.status })
}
"""
    assert _extract_proxy_targets(ts) == ["/api/data_stats"]


def test_proxy_target_parsing_with_dynamic_path_parameter() -> None:
    ts = """
export async function GET(req: NextRequest, { params }: { params: Promise<{ job_id: string }> }) {
  const { job_id } = await params
  const res = await fetch(`${ML_API_URL}/api/profiling/status/${job_id}`)
  return NextResponse.json(await res.json(), { status: res.status })
}
"""
    assert _extract_proxy_targets(ts) == ["/api/profiling/status/{param}"]


def test_shared_auth_helper_is_detected_as_authenticated() -> None:
    ts = """
export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request)
  if (!authz.ok) return NextResponse.json({ detail: authz.detail }, { status: authz.status })
  return NextResponse.json({ ok: true })
}
"""
    assert _detect_direct_policy(ts, "/api/example") == "Authenticated"


def test_backend_route_unknown_is_gate_failure() -> None:
    extracted = [
        type("_R", (), {
            "route": "/api/proxy-unknown",
            "method": "GET",
            "policy": "Authenticated",
            "backend_endpoint": "/api/does-not-exist",
            "backend_method": "GET",
            "backend_policy": "Unclassified",
            "auth_header_forward": True,
            "status_propagation": True,
            "detail_error_passthrough": True,
            "source": "src/app/api/proxy-unknown/route.ts",
        })()
    ]
    canonical = [
        {
            "route": "/api/proxy-unknown",
            "method": "GET",
            "policy": "Authenticated",
            "auth_header_forward": True,
            "status_propagation": True,
            "detail_error_passthrough": True,
            "auth_header_forward_reason": "",
            "status_propagation_reason": "",
            "detail_error_passthrough_reason": "",
        }
    ]
    failures = gate_next_routes(extracted, canonical)
    assert any(f.kind == "backend_unresolved" for f in failures)


def test_canonical_unclassified_is_gate_failure() -> None:
    extracted = [
        type("_R", (), {
            "route": "/api/a",
            "method": "GET",
            "policy": "Authenticated",
            "backend_endpoint": "",
            "backend_method": "",
            "backend_policy": "Unclassified",
            "auth_header_forward": True,
            "status_propagation": True,
            "detail_error_passthrough": True,
            "source": "src/app/api/a/route.ts",
        })()
    ]
    canonical = [{
        "route": "/api/a",
        "method": "GET",
        "policy": "Unclassified",
        "auth_header_forward": True,
        "status_propagation": True,
        "detail_error_passthrough": True,
        "auth_header_forward_reason": "",
        "status_propagation_reason": "",
        "detail_error_passthrough_reason": "",
    }]
    failures = gate_next_routes(extracted, canonical)
    assert any(f.kind == "unclassified_canonical" for f in failures)


def test_extracted_unclassified_is_gate_failure_even_if_canonical_matches() -> None:
    extracted = [
        type("_R", (), {
            "route": "/api/b",
            "method": "GET",
            "policy": "Unclassified",
            "backend_endpoint": "",
            "backend_method": "",
            "backend_policy": "Unclassified",
            "auth_header_forward": True,
            "status_propagation": True,
            "detail_error_passthrough": True,
            "source": "src/app/api/b/route.ts",
        })()
    ]
    canonical = [{
        "route": "/api/b",
        "method": "GET",
        "policy": "Unclassified",
        "auth_header_forward": True,
        "status_propagation": True,
        "detail_error_passthrough": True,
        "auth_header_forward_reason": "",
        "status_propagation_reason": "",
        "detail_error_passthrough_reason": "",
    }]
    failures = gate_next_routes(extracted, canonical)
    assert any(f.kind == "unclassified_extracted" for f in failures)
