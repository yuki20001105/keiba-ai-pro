"""Opt-in authenticated authorization diagnosis, NOT a five-feature release gate.

No network or credential-file access occurs without --authenticate. When opted
in, use the existing .env.local public Supabase settings and .env.test E2E
credentials in memory, perform ONE ordinary password sign-in, then THREE fixed
GETs. The ordinary identity-provider session creation is the only authorized
non-read operation. No autologin URLs, role changes, refresh, scraping, training,
prediction, settlement, purchase, restart, cookies, or JWT persistence occur.

The report contains only fixed endpoint names, status enums/codes and response
counts. Bodies, identities, secrets, provider hosts and exception messages are
never output. Capability/health GETs are intentionally excluded: their current
backend implementations can initialize storage or purge job memory. Performance
is excluded because it can settle records. Existing evidence is not replaced.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_RELATIVE = Path("reports/generated/local-release-smoke")
ALLOWED_ENDPOINTS = (
    "/api/models", "/api/races/by-date?date=20261010", "/api/admin/profiles",
)
TIMEOUT_SECONDS = 8
MAX_BODY_BYTES = 1024 * 1024
MAX_HEADER_SECRET_LENGTH = 16384
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
_PUBLIC_KEYS = frozenset({
    "NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "SUPABASE_ANON_KEY", "ML_API_URL", "NEXT_PUBLIC_API_URL",
})
_E2E_KEYS = frozenset({"E2E_EMAIL", "E2E_PASSWORD"})


class SafeSmokeError(Exception):
    """Only constant, identity-free error codes may leave an internal function."""


class Configuration:
    __slots__ = ("supabase_origin", "anon_key", "email", "password")

    def __init__(self, supabase_origin: str, anon_key: str, email: str, password: str):
        self.supabase_origin, self.anon_key = supabase_origin, anon_key
        self.email, self.password = email, password

    def __repr__(self):
        return "<LocalSmokeConfiguration redacted>"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def origin(value: str, *, auth: bool = False, backend: bool = False) -> str:
    """Never resolve a hostname to decide whether it is local."""
    try:
        parsed = urlsplit(value)
        port, host = parsed.port, parsed.hostname or ""
    except (ValueError, TypeError):
        raise SafeSmokeError("CONFIGURATION_REJECTED") from None
    local = host == "127.0.0.1" or (backend and host == "localhost")
    hosted_auth = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.supabase\.co", host))
    target_allowed = (
        (local and parsed.scheme in {"http", "https"})
        or (auth and hosted_auth and parsed.scheme == "https" and port in (None, 443))
    )
    if (not target_allowed or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or port == 0 or "\\" in value or any(ord(char) < 32 for char in value)):
        raise SafeSmokeError("CONFIGURATION_REJECTED") from None
    return value.rstrip("/")


def selected_dotenv(path: Path, keys: frozenset[str]) -> dict[str, str]:
    """Read selected one-line values, without eval, interpolation or env export."""
    result = {}
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                text = line.strip()
                if text.startswith("export "):
                    text = text[7:].lstrip()
                key, separator, value = text.partition("=")
                key = key.strip()
                if not separator or key not in keys:
                    continue  # No service-role key is retained or used.
                value = value.strip()
                if value.startswith(("'", '"')):
                    if len(value) < 2 or value[-1] != value[0]:
                        raise SafeSmokeError("CONFIGURATION_REJECTED")
                    value = value[1:-1]
                else:
                    value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
                if not value or "\x00" in value:
                    raise SafeSmokeError("CONFIGURATION_REJECTED")
                result[key] = value
    except (OSError, UnicodeError):
        raise SafeSmokeError("CONFIGURATION_UNAVAILABLE") from None
    return result


def public_anon_key(value: str) -> str:
    if (not isinstance(value, str) or len(value) > MAX_HEADER_SECRET_LENGTH
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise SafeSmokeError("CONFIGURATION_REJECTED")
    if value.startswith("sb_publishable_") and len(value) > len("sb_publishable_"):
        return value
    if not _JWT.fullmatch(value):
        raise SafeSmokeError("CONFIGURATION_REJECTED")
    try:
        payload = value.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        raise SafeSmokeError("CONFIGURATION_REJECTED") from None
    if not isinstance(claims, dict) or claims.get("role") != "anon":
        raise SafeSmokeError("CONFIGURATION_REJECTED")
    return value


def configuration(root: Path) -> Configuration:
    settings = selected_dotenv(root / ".env.local", _PUBLIC_KEYS)
    credentials = selected_dotenv(root / ".env.test", _E2E_KEYS)
    try:
        provider = settings.get("NEXT_PUBLIC_SUPABASE_URL") or settings["SUPABASE_URL"]
        key = settings.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or settings["SUPABASE_ANON_KEY"]
        email, password = credentials["E2E_EMAIL"], credentials["E2E_PASSWORD"]
    except KeyError:
        raise SafeSmokeError("CONFIGURATION_UNAVAILABLE") from None
    # The inspected models/races frontend handlers forward the bearer token to
    # ML_API_URL. Reject a known remote upstream in the existing file settings;
    # this is not a claim that files prove the running server's environment.
    origin(settings.get("ML_API_URL") or settings.get("NEXT_PUBLIC_API_URL")
           or "http://localhost:8000", backend=True)
    if len(email) > 1024 or len(password) > 4096:
        raise SafeSmokeError("CONFIGURATION_REJECTED")
    return Configuration(origin(provider, auth=True), public_anon_key(key), email, password)


def secure_opener():
    # Explicit empty proxies ignore HTTP(S)_PROXY/ALL_PROXY and OS proxy config.
    # No cookie handler is installed and TLS certificate verification is on.
    return build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))


def bounded_json(response, deadline: float):
    read = getattr(response, "read1", None) or response.read
    pieces, length = [], 0
    while True:
        if time.monotonic() > deadline:
            raise SafeSmokeError("RESPONSE_REJECTED")
        chunk = read(min(16384, MAX_BODY_BYTES + 1 - length))
        if not chunk:
            break
        length += len(chunk)
        if length > MAX_BODY_BYTES:
            raise SafeSmokeError("RESPONSE_REJECTED")
        pieces.append(chunk)
    try:
        return json.loads(b"".join(pieces))
    except (ValueError, UnicodeError):
        raise SafeSmokeError("RESPONSE_REJECTED") from None


def request_json(opener, request: Request):
    """No retry, redirect following, error-body reading or raw exception text."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                return "RESPONSE_REJECTED", None
            if status != 200:
                return status, None
            return status, bounded_json(response, deadline)
    except HTTPError as exc:
        status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else "RESPONSE_REJECTED"
        exc.close()  # Do not read or retain private authentication error bodies.
        return status, None
    except SafeSmokeError:
        return "RESPONSE_REJECTED", None
    except Exception:
        return "TRANSPORT_FAILED", None


def authenticate(opener, config: Configuration):
    provider = origin(config.supabase_origin, auth=True)
    key = public_anon_key(config.anon_key)
    body = json.dumps({"email": config.email, "password": config.password}).encode("utf-8")
    request = Request(
        provider + "/auth/v1/token?grant_type=password", data=body, method="POST",
        headers={"apikey": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    status, response = request_json(opener, request)
    if status != 200 or not isinstance(response, dict):
        return status, None
    token = response.get("access_token")
    if not isinstance(token, str) or len(token) > MAX_HEADER_SECRET_LENGTH or not _JWT.fullmatch(token):
        return "RESPONSE_REJECTED", None
    return 200, token  # The body, refresh token and user fields are discarded.


def probe(opener, base: str, endpoint: str, token: str) -> dict:
    if endpoint not in ALLOWED_ENDPOINTS:
        raise SafeSmokeError("ENDPOINT_REJECTED")
    base = origin(base)
    if not isinstance(token, str) or len(token) > MAX_HEADER_SECRET_LENGTH or not _JWT.fullmatch(token):
        raise SafeSmokeError("TOKEN_REJECTED")
    request = Request(base + endpoint, method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
    })
    status, body = request_json(opener, request)
    count = None
    if status == 200 and isinstance(body, dict):
        collection = body.get({ALLOWED_ENDPOINTS[0]: "models", ALLOWED_ENDPOINTS[1]: "races", ALLOWED_ENDPOINTS[2]: "profiles"}[endpoint])
        if isinstance(collection, list):
            count = len(collection)  # Never copy a row, ID, name or server count.
        else:
            status = "RESPONSE_REJECTED"
    elif status == 200:
        status = "RESPONSE_REJECTED"
    return {"endpoint": endpoint, "status": status, "count": count}


def run_smoke(*, authenticate_requested: bool, root: Path, base: str, opener=None) -> dict:
    report = {
        "schema": "local-authenticated-readonly-smoke-v1",
        "scope": "authenticated_local_authorization_diagnosis_only",
        "authentication_status": "NOT_RUN",
        "admin_read_authorized": False,
        "probes": [{"endpoint": endpoint, "status": "NOT_RUN", "count": None} for endpoint in ALLOWED_ENDPOINTS],
        "capability_checked": False, "all_five_features_completed": False,
        "production_release_authorized": False, "profitability_validated": False,
        "count_scope": "response_list_length_only_not_total_inventory",
        "running_upstream_environment_binding_verified": False,
    }
    if not authenticate_requested:
        return report
    try:
        base = origin(base)
        config = configuration(root)
        opener = opener or secure_opener()
        status, token = authenticate(opener, config)
        report["authentication_status"] = status
        if status != 200 or token is None:
            return report
        report["probes"] = [probe(opener, base, endpoint, token) for endpoint in ALLOWED_ENDPOINTS]
        admin = report["probes"][2]
        report["admin_read_authorized"] = admin["status"] == 200 and admin["count"] is not None
        # No mutation uses this diagnostic boolean, and no role is inferred
        # from an unverified JWT claim. Only the protected GET's result matters.
        token = None
    except SafeSmokeError:
        report["authentication_status"] = "CONFIGURATION_REJECTED"
    except Exception:
        report["authentication_status"] = "DIAGNOSTIC_FAILED"
    return report


def report_path(root: Path, supplied: Path | None) -> Path:
    allowed = (root / OUTPUT_RELATIVE).resolve()
    if not allowed.is_relative_to(root.resolve()):
        raise SafeSmokeError("OUTPUT_REJECTED")
    target = supplied or allowed / ("authenticated-readonly-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    target = target.resolve()
    if not target.is_relative_to(allowed) or target.suffix != ".json" or target.exists():
        raise SafeSmokeError("OUTPUT_REJECTED")
    return target


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authenticate", action="store_true", help="Opt into one ordinary password sign-in and three fixed read-only GETs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    try:
        output = report_path(ROOT, args.output)
        # Validate the output destination before reading secrets or signing in.
        report = run_smoke(authenticate_requested=args.authenticate, root=ROOT, base=args.base_url)
        write_report(output, report)
    except Exception:
        print("Local authorization diagnostic: OUTPUT_REJECTED")
        return 1
    print("Evidence: " + str(output.relative_to(ROOT)))
    print("Scope: authenticated read-only authorization diagnosis; five-feature completion NOT established.")
    return 0 if report["authentication_status"] == 200 and all(probe["status"] == 200 for probe in report["probes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
