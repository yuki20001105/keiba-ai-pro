from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "python-api") not in sys.path:
    sys.path.insert(0, str(ROOT / "python-api"))

from fastapi.routing import APIRoute  # type: ignore
import main as fastapi_main  # type: ignore


PUBLIC_ALLOWLIST = {
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

INTERNAL_DEP_NAMES = {
    "require_internal_secret",
}

ADMIN_DEP_NAMES = {
    "require_admin",
}
PREMIUM_DEP_NAMES = {
    "require_premium",
}

NEXT_POLICY_CANONICAL_PATH = ROOT / "docs" / "specs" / "PHASE2_NEXT_ROUTE_POLICY.json"
NEXT_API_DIR = ROOT / "src" / "app" / "api"

GENERIC_REASON_VALUES = {
    "public/webhook/internal route does not forward end-user Authorization header",
    "route returns normalized response envelope instead of upstream status passthrough",
    "route maps upstream errors to fixed error schema without passthrough detail",
}

DIRECT_PUBLIC_ALLOWLIST = {
    "/api/health",
    "/api/stripe/webhook",
}


@dataclass(frozen=True)
class FastAPIRouteInfo:
    endpoint: str
    method: str
    policy: str
    dependency_names: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class NextRouteInfo:
    route: str
    method: str
    policy: str
    backend_endpoint: str
    backend_method: str
    backend_policy: str
    auth_header_forward: bool
    status_propagation: bool
    detail_error_passthrough: bool
    source: str


@dataclass(frozen=True)
class GateFailure:
    kind: str
    key: str
    detail: str


def _callable_name(obj: Any) -> str:
    if obj is None:
        return ""
    if hasattr(obj, "__name__"):
        return str(obj.__name__)
    if hasattr(obj, "func") and hasattr(obj.func, "__name__"):
        return str(obj.func.__name__)
    return obj.__class__.__name__


def _extract_dependency_names(dep: Any) -> set[str]:
    names: set[str] = set()
    call = getattr(dep, "call", None)
    if call is not None:
        name = _callable_name(call)
        if name:
            names.add(name)
    for child in getattr(dep, "dependencies", []) or []:
        names |= _extract_dependency_names(child)
    return names


def classify_fastapi_route(path: str, dependency_names: Iterable[str]) -> str:
    return classify_fastapi_route_with_runtime(
        path=path,
        dependency_names=dependency_names,
        jwt_middleware_targets_api=False,
        exempt_paths=PUBLIC_ALLOWLIST,
    )


def _resolve_jwt_middleware_config() -> tuple[bool, set[str]]:
    for middleware in getattr(fastapi_main.app, "user_middleware", []) or []:
        cls = getattr(middleware, "cls", None)
        cls_name = getattr(cls, "__name__", "")
        if cls_name != "SupabaseJWTMiddleware":
            continue
        kwargs = getattr(middleware, "kwargs", {}) or {}
        exempt_paths = set(kwargs.get("exempt_paths") or set())
        return True, exempt_paths
    return False, set()


def _is_jwt_guard_target(path: str, exempt_paths: set[str]) -> bool:
    if path in exempt_paths:
        return False
    if not path.startswith("/api/"):
        return False
    if path.startswith("/api/internal/"):
        return False
    return True


def classify_fastapi_route_with_runtime(
    path: str,
    dependency_names: Iterable[str],
    *,
    jwt_middleware_targets_api: bool,
    exempt_paths: set[str],
) -> str:
    deps = set(dependency_names)
    if path in exempt_paths:
        return "Public"
    if path.startswith("/api/internal/"):
        return "Internal" if deps & INTERNAL_DEP_NAMES else "Unclassified"
    if deps & ADMIN_DEP_NAMES:
        return "Admin"
    if deps & PREMIUM_DEP_NAMES:
        return "PremiumOrAdmin"
    if jwt_middleware_targets_api:
        return "Authenticated"
    if path.startswith("/api/"):
        return "Unclassified"
    return "Public"


def extract_fastapi_runtime_routes() -> list[FastAPIRouteInfo]:
    has_jwt_middleware, exempt_paths = _resolve_jwt_middleware_config()
    out: list[FastAPIRouteInfo] = []
    for route in fastapi_main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = sorted(m for m in (route.methods or set()) if m not in {"HEAD", "OPTIONS"})
        dep_names = tuple(sorted(_extract_dependency_names(route.dependant)))
        policy = classify_fastapi_route_with_runtime(
            route.path,
            dep_names,
            jwt_middleware_targets_api=has_jwt_middleware and _is_jwt_guard_target(route.path, exempt_paths),
            exempt_paths=exempt_paths or set(PUBLIC_ALLOWLIST),
        )
        source = ""
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            source_file = getattr(endpoint, "__code__", None)
            if source_file is not None:
                try:
                    source = str(Path(source_file.co_filename).resolve().relative_to(ROOT).as_posix())
                except Exception:
                    source = str(source_file.co_filename)
        for method in methods:
            out.append(
                FastAPIRouteInfo(
                    endpoint=route.path,
                    method=method,
                    policy=policy,
                    dependency_names=dep_names,
                    source=source,
                )
            )
    out.sort(key=lambda x: (x.endpoint, x.method, x.source))
    return out


def _next_route_path(route_file: Path) -> str:
    rel = route_file.relative_to(NEXT_API_DIR).as_posix()
    rel = rel[: -len("/route.ts")]
    return "/api/" + rel


def _extract_http_methods(ts: str) -> list[str]:
    methods = re.findall(r"export\s+async\s+function\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(", ts)
    return sorted(set(methods))


def _extract_method_block(ts: str, method: str) -> str:
    m = re.search(rf"export\s+async\s+function\s+{method}\s*\(", ts)
    if not m:
        return ts
    start = m.start()
    rest = ts[m.end():]
    n = re.search(r"export\s+async\s+function\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(", rest)
    end = m.end() + n.start() if n else len(ts)
    return ts[start:end]


def _extract_request_param_names(ts: str) -> set[str]:
    return set(re.findall(r"(?:^|[,\(\s])([A-Za-z_]\w*)\s*:\s*(?:NextRequest|Request)\b", ts))


def _detect_auth_header_read(ts: str) -> bool:
    if "verifyRequestAuth(" in ts:
        return True
    req_names = _extract_request_param_names(ts)
    for name in req_names:
        if re.search(rf"\b{name}\.headers\.get\((['\"])Authorization\1\)", ts):
            return True
    return bool(re.search(r"\bheaders\.get\((['\"])Authorization\1\)", ts))


def _detect_auth_header_forward(ts: str) -> bool:
    if re.search(r"Authorization\s*:\s*[^,}\n]+", ts):
        return True
    if re.search(r"headers\s*\.\s*Authorization\s*=", ts):
        return True
    return False


def _extract_auth_helper_policies(ts: str) -> dict[str, str]:
    out: dict[str, str] = {}
    starts = list(re.finditer(r"(?:^|\n)\s*(?:export\s+)?async\s+function\s+([A-Za-z_]\w*)\s*\(", ts))
    for idx, m in enumerate(starts):
        name = m.group(1)
        start = m.start()
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(ts)
        body = ts[start:end]

        policy = ""
        if "requireAdmin: true" in body or re.search(r"role\s*!==\s*['\"]admin['\"]", body):
            policy = "Admin"
        elif (
            "requirePremiumOrAdmin: true" in body
            or "Premium or admin role required" in body
            or re.search(r"tier\s*!==\s*['\"]premium['\"]", body)
        ):
            policy = "PremiumOrAdmin"
        elif ".auth.getUser()" in body or "Authentication required" in body or "認証が必要" in body:
            policy = "Authenticated"

        if policy:
            out[name] = policy
    return out


def _detect_direct_policy(ts: str, route_path: str, helper_policies: dict[str, str] | None = None) -> str:
    if route_path.startswith("/api/stripe/webhook"):
        return "Webhook"
    if route_path.startswith("/api/internal/"):
        return "Internal"
    if "verifyRequestAuth(" in ts and "requireAdmin: true" in ts:
        return "Admin"
    if "verifyRequestAuth(" in ts and "requirePremiumOrAdmin: true" in ts:
        return "PremiumOrAdmin"
    if "verifyRequestAuth(" in ts or ".auth.getUser()" in ts:
        return "Authenticated"

    helpers = helper_policies or {}
    called_helpers = re.findall(r"\bawait\s+([A-Za-z_]\w*)\s*\(", ts)
    for helper in called_helpers:
        if helper in helpers:
            return helpers[helper]

    if re.search(r"\bauthz\s*=\s*await\s+[A-Za-z_]\w*\s*\(", ts) and "!authz.ok" in ts:
        return "Authenticated"

    if route_path in DIRECT_PUBLIC_ALLOWLIST:
        return "Public"
    return "Unclassified"


def _skip_js_string(code: str, i: int) -> int:
    quote = code[i]
    i += 1
    while i < len(code):
        c = code[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return i


def _scan_template_literal(code: str, i: int) -> tuple[list[tuple[str, str]], int]:
    # Returns segments as [("lit"|"expr", value), ...]
    if i >= len(code) or code[i] != "`":
        return [], i

    segments: list[tuple[str, str]] = []
    i += 1
    lit_buf: list[str] = []

    while i < len(code):
        c = code[i]
        if c == "\\":
            if i + 1 < len(code):
                lit_buf.append(code[i + 1])
                i += 2
                continue
            i += 1
            continue
        if c == "`":
            if lit_buf:
                segments.append(("lit", "".join(lit_buf)))
            return segments, i + 1
        if c == "$" and i + 1 < len(code) and code[i + 1] == "{":
            if lit_buf:
                segments.append(("lit", "".join(lit_buf)))
                lit_buf = []
            i += 2
            depth = 1
            expr_start = i
            while i < len(code) and depth > 0:
                ec = code[i]
                if ec in {"'", '"'}:
                    i = _skip_js_string(code, i)
                    continue
                if ec == "`":
                    nested, nested_end = _scan_template_literal(code, i)
                    if nested_end == i:
                        i += 1
                    else:
                        i = nested_end
                    continue
                if ec == "{":
                    depth += 1
                elif ec == "}":
                    depth -= 1
                    if depth == 0:
                        expr = code[expr_start:i].strip()
                        segments.append(("expr", expr))
                        i += 1
                        break
                i += 1
            continue
        lit_buf.append(c)
        i += 1
    return segments, i


def _normalize_backend_path(path: str) -> str:
    p = path.split("?", 1)[0].strip()
    p = p.replace("[", "{").replace("]", "}")
    p = re.sub(r"\{\s*([A-Za-z_]\w*)\s*\}", r"{\1}", p)
    p = re.sub(r"\{[^}]*\}", "{param}", p)
    p = re.sub(r"//+", "/", p)
    return p


def _param_name_from_expr(expr: str) -> str:
    m = re.search(r"([A-Za-z_]\w*)\s*$", expr)
    return m.group(1) if m else "param"


def _is_path_param_expr(expr: str) -> bool:
    if not expr:
        return False
    lowered = expr.lower()
    if "?" in expr or "searchparams" in lowered or "query" in lowered or "encodeuri" in lowered:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][\w.]*", expr))


def _extract_backend_path_from_template_segments(segments: list[tuple[str, str]]) -> str:
    if not segments:
        return ""
    if segments[0][0] != "expr" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", segments[0][1]):
        return ""
    url_var = segments[0][1]
    if not (url_var.endswith("_API_URL") or url_var == "API_URL"):
        return ""

    out_parts: list[str] = []
    i = 1
    while i < len(segments):
        kind, val = segments[i]
        next_lit = ""
        if i + 1 < len(segments) and segments[i + 1][0] == "lit":
            next_lit = segments[i + 1][1]

        if kind == "lit":
            if not out_parts and "/api/" not in val:
                i += 1
                continue
            part = val
            if not out_parts:
                part = part[part.find("/api/"):]
            q_idx = part.find("?")
            if q_idx >= 0:
                out_parts.append(part[:q_idx])
                break
            out_parts.append(part)
            i += 1
            continue

        # Interpolation inside route path or query tail.
        if out_parts and _is_path_param_expr(val) and (out_parts[-1].endswith("/") or next_lit.startswith("/")):
            out_parts.append("{" + _param_name_from_expr(val) + "}")
            i += 1
            continue
        if out_parts:
            break
        i += 1

    candidate = "".join(out_parts)
    if "/api/" not in candidate:
        return ""
    return _normalize_backend_path(candidate)


def _extract_template_const_paths(ts: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"\bconst\s+([A-Za-z_]\w*)\s*=\s*`", ts):
        var_name = m.group(1)
        segs, _ = _scan_template_literal(ts, m.end() - 1)
        path = _extract_backend_path_from_template_segments(segs)
        if path:
            out[var_name] = path
    return out


def _extract_proxy_targets(ts: str) -> list[str]:
    targets: list[str] = []
    const_paths = _extract_template_const_paths(ts)

    for m in re.finditer(r"\bfetch\s*\(\s*", ts):
        i = m.end()
        while i < len(ts) and ts[i].isspace():
            i += 1
        if i >= len(ts):
            continue

        if ts[i] == "`":
            segs, _ = _scan_template_literal(ts, i)
            path = _extract_backend_path_from_template_segments(segs)
            if path:
                targets.append(path)
            continue

        if ts[i] in {"'", '"'}:
            q = ts[i]
            j = i + 1
            while j < len(ts):
                if ts[j] == "\\":
                    j += 2
                    continue
                if ts[j] == q:
                    break
                j += 1
            literal = ts[i + 1:j]
            if literal.startswith("/api/"):
                targets.append(_normalize_backend_path(literal))
            continue

        ident = re.match(r"([A-Za-z_]\w*)", ts[i:])
        if ident:
            name = ident.group(1)
            if name in const_paths:
                targets.append(const_paths[name])

    for m in re.finditer(r"proxy\w*\([^,]+,\s*(['\"])(/api/[^'\"]+)\1", ts):
        targets.append(_normalize_backend_path(m.group(2)))

    # Preserve discovery order while deduplicating.
    seen: set[str] = set()
    ordered: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _extract_first_backend_method(ts: str) -> str | None:
    m = re.search(r"\bfetch\s*\([\s\S]*?\bmethod\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", ts)
    if not m:
        return None
    return m.group(1)


def _path_matches(pattern: str, concrete: str) -> bool:
    regex = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", pattern) + "$"
    return bool(re.match(regex, concrete))


def _find_fastapi_policy(path: str, method: str, fastapi_routes: list[FastAPIRouteInfo]) -> str:
    for r in fastapi_routes:
        if r.method == method and r.endpoint == path:
            return r.policy
    for r in fastapi_routes:
        if r.method == method and _path_matches(r.endpoint, path):
            return r.policy
    return "Unclassified"


def _find_fastapi_policy_by_next_style(path: str, method: str, fastapi_routes: list[FastAPIRouteInfo]) -> str:
    # Try direct and parameterized matching for extracted /api/... templates.
    direct = _find_fastapi_policy(path, method, fastapi_routes)
    if direct != "Unclassified":
        return direct
    next_style = re.sub(r"\{[A-Za-z_]\w*\}", "{param}", path)
    for r in fastapi_routes:
        if r.method != method:
            continue
        fa_style = re.sub(r"\{[A-Za-z_]\w*\}", "{param}", r.endpoint)
        if fa_style == next_style:
            return r.policy
    return "Unclassified"


def _infer_next_infos(route_path: str, ts: str, methods: list[str], fastapi_routes: list[FastAPIRouteInfo], source: str) -> list[NextRouteInfo]:
    out: list[NextRouteInfo] = []
    helper_policies = _extract_auth_helper_policies(ts)

    for method in methods:
        block = _extract_method_block(ts, method)
        has_auth_get = _detect_auth_header_read(block)
        has_auth_set = _detect_auth_header_forward(block)
        status_passthrough = bool(re.search(r"status\s*:\s*(response|res)\.status", block))
        detail_passthrough = bool(
            re.search(r"NextResponse\.json\(\s*data\s*(?:,|\))", block)
            or re.search(r"\{\s*detail\s*:\s*(?:message|errorData\.detail|data\.detail)\s*\}", block)
        )
        proxy_targets = _extract_proxy_targets(block)

        backend_endpoint = ""
        backend_method = ""
        backend_policy = "Unclassified"
        policy = "Unclassified"

        if proxy_targets:
            backend_endpoint = proxy_targets[0]
            backend_method = _extract_first_backend_method(block) or method
            backend_policy = _find_fastapi_policy_by_next_style(backend_endpoint, backend_method, fastapi_routes)
            if backend_policy == "Public":
                policy = "Public"
            elif backend_policy in {"Authenticated", "Admin", "PremiumOrAdmin", "Internal"}:
                policy = backend_policy if (has_auth_get and has_auth_set) else "Unclassified"
            else:
                policy = "Unclassified"
        else:
            policy = _detect_direct_policy(block, route_path, helper_policies)

        out.append(
            NextRouteInfo(
                route=route_path,
                method=method,
                policy=policy,
                backend_endpoint=backend_endpoint,
                backend_method=backend_method,
                backend_policy=backend_policy,
                auth_header_forward=bool(has_auth_get and has_auth_set),
                status_propagation=status_passthrough,
                detail_error_passthrough=detail_passthrough,
                source=source,
            )
        )
    return out


def extract_next_routes() -> list[NextRouteInfo]:
    fastapi_routes = extract_fastapi_runtime_routes()
    out: list[NextRouteInfo] = []
    for route_file in sorted(NEXT_API_DIR.rglob("route.ts")):
        ts = route_file.read_text(encoding="utf-8")
        methods = _extract_http_methods(ts)
        if not methods:
            continue
        route_path = _next_route_path(route_file)
        source = route_file.relative_to(ROOT).as_posix()
        out.extend(_infer_next_infos(route_path, ts, methods, fastapi_routes, source))
    out.sort(key=lambda x: (x.route, x.method, x.source))
    return out


def _key(path: str, method: str) -> str:
    return f"{method} {path}"


def _duplicates(keys: Iterable[str]) -> list[str]:
    c = Counter(keys)
    return sorted([k for k, v in c.items() if v > 1])


def load_next_policy_canonical() -> list[dict[str, Any]]:
    data = json.loads(NEXT_POLICY_CANONICAL_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("PHASE2_NEXT_ROUTE_POLICY.json must be a list")
    return data


def gate_next_routes(extracted: list[NextRouteInfo], canonical: list[dict[str, Any]]) -> list[GateFailure]:
    failures: list[GateFailure] = []

    ext_keys = [_key(r.route, r.method) for r in extracted]
    can_keys = [_key(str(r.get("route", "")), str(r.get("method", ""))) for r in canonical]

    for k in _duplicates(ext_keys):
        failures.append(GateFailure(kind="duplicate_extracted", key=k, detail="duplicate route/method in extracted next routes"))
    for k in _duplicates(can_keys):
        failures.append(GateFailure(kind="duplicate_canonical", key=k, detail="duplicate route/method in canonical next policy"))

    ext_map = {_key(r.route, r.method): r for r in extracted}
    can_map = {_key(str(r.get("route", "")), str(r.get("method", ""))): r for r in canonical}

    for k in sorted(set(ext_map) - set(can_map)):
        failures.append(GateFailure(kind="unexpected_route", key=k, detail="route exists in source but not canonical"))
    for k in sorted(set(can_map) - set(ext_map)):
        failures.append(GateFailure(kind="missing_route", key=k, detail="route exists in canonical but not source"))

    for k in sorted(set(ext_map) & set(can_map)):
        ext = ext_map[k]
        can = can_map[k]
        expected_policy = str(can.get("policy", "")).strip()
        if not expected_policy:
            failures.append(GateFailure(kind="unclassified_canonical", key=k, detail="canonical policy is empty"))
        elif expected_policy == "Unclassified":
            failures.append(GateFailure(kind="unclassified_canonical", key=k, detail="canonical policy must not be Unclassified"))
        if ext.policy == "Unclassified":
            failures.append(GateFailure(kind="unclassified_extracted", key=k, detail="extracted policy must not be Unclassified"))
        elif ext.policy != expected_policy:
            failures.append(
                GateFailure(
                    kind="mismatch",
                    key=k,
                    detail=f"policy: expected={expected_policy} actual={ext.policy}",
                )
            )

        if ext.backend_endpoint:
            if ext.backend_policy == "Unclassified":
                failures.append(
                    GateFailure(
                        kind="backend_unresolved",
                        key=k,
                        detail=f"proxy backend route unresolved: {ext.backend_method} {ext.backend_endpoint}",
                    )
                )
            elif ext.policy != ext.backend_policy:
                failures.append(
                    GateFailure(
                        kind="backend_policy_mismatch",
                        key=k,
                        detail=f"next policy={ext.policy} backend policy={ext.backend_policy}",
                    )
                )

        reason_fields = {
            "auth_header_forward": "auth_header_forward_reason",
            "status_propagation": "status_propagation_reason",
            "detail_error_passthrough": "detail_error_passthrough_reason",
        }
        checks = {
            "auth_header_forward": ext.auth_header_forward,
            "status_propagation": ext.status_propagation,
            "detail_error_passthrough": ext.detail_error_passthrough,
        }
        for field, actual in checks.items():
            expected = bool(can.get(field))
            if not expected:
                reason = str(can.get(reason_fields[field], "")).strip()
                if not reason:
                    failures.append(
                        GateFailure(
                            kind="unclassified_canonical",
                            key=k,
                            detail=f"{reason_fields[field]} is required when {field}=false",
                        )
                    )
                elif reason in GENERIC_REASON_VALUES:
                    failures.append(
                        GateFailure(
                            kind="unclassified_canonical",
                            key=k,
                            detail=f"{reason_fields[field]} must be route-specific",
                        )
                    )
            if actual != expected:
                failures.append(
                    GateFailure(
                        kind="mismatch",
                        key=k,
                        detail=f"{field}: expected={expected} actual={actual}",
                    )
                )
    return failures


def build_matrix_payload() -> dict[str, Any]:
    fastapi_routes = extract_fastapi_runtime_routes()
    next_routes = extract_next_routes()
    canonical = load_next_policy_canonical()

    unclassified = [asdict(r) for r in fastapi_routes if r.policy == "Unclassified"]
    fastapi_dupes = _duplicates([_key(r.endpoint, r.method) for r in fastapi_routes])
    next_unclassified = [asdict(r) for r in next_routes if r.policy == "Unclassified"]
    next_failures = [asdict(f) for f in gate_next_routes(next_routes, canonical)]

    return {
        "fastapi": {
            "count": len(fastapi_routes),
            "routes": [asdict(r) for r in fastapi_routes],
            "unclassified": unclassified,
            "duplicates": fastapi_dupes,
        },
        "next": {
            "count": len(next_routes),
            "routes": [asdict(r) for r in next_routes],
            "unclassified": next_unclassified,
            "canonical_entries": len(canonical),
            "gate_failures": next_failures,
        },
        "gate": {
            "fastapi_unclassified_count": len(unclassified),
            "fastapi_duplicate_count": len(fastapi_dupes),
            "next_unclassified_count": len(next_unclassified),
            "next_failure_count": len(next_failures),
            "pass": len(unclassified) == 0 and len(fastapi_dupes) == 0 and len(next_unclassified) == 0 and len(next_failures) == 0,
        },
    }


def sync_next_canonical_from_source() -> list[dict[str, Any]]:
    routes = extract_next_routes()
    out = []
    for r in routes:
        out.append(
            {
                "route": r.route,
                "method": r.method,
                "policy": r.policy,
                "auth_header_forward": r.auth_header_forward,
                "status_propagation": r.status_propagation,
                "detail_error_passthrough": r.detail_error_passthrough,
                "auth_header_forward_reason": "",
                "status_propagation_reason": "",
                "detail_error_passthrough_reason": "",
            }
        )
    NEXT_POLICY_CANONICAL_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
