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
    _extract_auth_helper_policies,
    _extract_json_passthrough_helpers,
    _extract_proxy_targets,
    _infer_next_infos,
    FastAPIRouteInfo,
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


def test_encoded_identifier_is_still_a_route_parameter_not_a_query() -> None:
    ts = """
export async function PUT(request: NextRequest) {
  const response = await fetch(`${ML_API_URL}/api/models/${encodeURIComponent(id)}/activate`, { method: 'PUT' })
}
"""
    assert _extract_proxy_targets(ts) == ["/api/models/{param}/activate"]


def test_encoded_arbitrary_expression_is_not_treated_as_a_path_parameter() -> None:
    ts = """
export async function PUT(request: NextRequest) {
  const response = await fetch(`${ML_API_URL}/api/models/${encodeURIComponent(id + '/unsafe')}/activate`)
}
"""
    assert _extract_proxy_targets(ts) != ["/api/models/{param}/activate"]


def test_pure_no_store_helper_proves_upstream_status_and_body_passthrough() -> None:
    ts = """
function noStoreJson(body: unknown, status: number) {
  return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } })
}
export async function DELETE(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  const response = await fetch(`${ML_API_URL}/api/models/${encodeURIComponent(id)}`, {
    method: 'DELETE', headers: { Authorization: `Bearer ${authz.context.token}` },
  })
  const data: unknown = await response.json().catch(() => null)
  return noStoreJson(data, response.status)
}
"""
    backend = [FastAPIRouteInfo("/api/models/{model_id}", "DELETE", "Admin", ("require_admin",), "fixture")]
    info = _infer_next_infos("/api/models/[id]", ts, ["DELETE"], backend, "fixture")[0]
    assert info.backend_endpoint == "/api/models/{param}"
    assert info.backend_policy == info.policy == "Admin"
    assert info.status_propagation and info.detail_error_passthrough


def test_json_helper_must_not_change_payload_status_or_execute_other_statements() -> None:
    helpers = (
        "function wrapped(body: unknown, status: number) { return NextResponse.json({ ok: true }, { status, headers: { 'Cache-Control': 'no-store' } }) }",
        "function wrapped(body: unknown, status: number) { return NextResponse.json(body, { status: 200, headers: { 'Cache-Control': 'no-store' } }) }",
        "function wrapped(body: unknown, status: number) { status = 200; return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } }) }",
        "function wrapped(body: unknown, code: number) { return NextResponse.json(body, { code, headers: { 'Cache-Control': 'no-store' } }) }",
    )
    for helper in helpers:
        assert not _extract_json_passthrough_helpers(helper)
        ts = helper + "\nexport async function GET(request: NextRequest) { return wrapped(data, response.status) }"
        info = _infer_next_infos("/api/fixture", ts, ["GET"], [], "fixture")[0]
        assert not info.status_propagation
        assert not info.detail_error_passthrough


def test_registered_personal_performance_routes_are_authenticated_not_public() -> None:
    routes = {(row.route, row.method): row for row in extract_next_routes()}
    for path, method in (("/api/performance", "GET"), ("/api/performance/records", "POST")):
        row = routes[(path, method)]
        assert row.policy == row.backend_policy == "Authenticated"
        assert row.auth_header_forward and row.status_propagation and row.detail_error_passthrough
    row = routes[("/api/prediction-explanation", "POST")]
    assert row.policy == "PremiumOrAdmin"
    assert not row.auth_header_forward


def test_current_source_authorization_gate_passes_without_unclassified_routes() -> None:
    failures = gate_next_routes(extract_next_routes(), load_next_policy_canonical())
    assert not failures, failures


def test_shared_auth_helper_is_detected_as_authenticated() -> None:
    ts = """
export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request)
  if (!authz.ok) return NextResponse.json({ detail: authz.detail }, { status: authz.status })
  return NextResponse.json({ ok: true })
}
"""
    assert _detect_direct_policy(ts, "/api/example") == "Authenticated"


def test_admin_mode_guard_is_detected_as_admin() -> None:
    ts = """
export async function GET(request: NextRequest) {
  const authz = await verifyRequestAuth(request, { requireAdminMode: true })
  if (!authz.ok) return NextResponse.json({ detail: authz.detail }, { status: authz.status })
  return NextResponse.json({ ok: true })
}
"""
    assert _detect_direct_policy(ts, "/api/admin/profiles") == "Admin"


def test_admin_unlock_delete_is_an_explicit_public_lock_action() -> None:
    ts = """
export async function DELETE() {
  return NextResponse.json({ unlocked: false })
}
"""
    assert _detect_direct_policy(ts, "/api/admin/unlock") == "Public"


def test_custom_premium_or_admin_helper_is_detected() -> None:
    ts = """
async function authorizePremiumOrAdmin(request: Request) {
  const role = String(profile.role || '').toLowerCase()
  const tier = String(profile.subscription_tier || '').toLowerCase()
  const isAdmin = role === 'admin'
  const isPremium = isAdmin || tier === 'premium'
  if (!isPremium) return { ok: false, status: 403 }
  return { ok: true, status: 200 }
}

export async function GET(request: Request) {
  const authz = await authorizePremiumOrAdmin(request)
  if (!authz.ok) return NextResponse.json({ detail: authz.detail }, { status: authz.status })
  return NextResponse.json({ ok: true })
}
"""
    policies = _extract_auth_helper_policies(ts)
    assert policies["authorizePremiumOrAdmin"] == "PremiumOrAdmin"
    assert _detect_direct_policy(ts, "/api/model-redesign/summary", policies) == "PremiumOrAdmin"


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
