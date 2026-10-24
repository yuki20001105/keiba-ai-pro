from __future__ import annotations

import base64
import importlib.util
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("local_authenticated_smoke", ROOT / "scripts/smoke_local_authenticated_readonly.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
TOKEN = "fixture.privatejwt.signature"
EMAIL, PASSWORD, OWNER = "fixture-account@example.test", "fixture-password-secret", "fixture-private-owner"


def env_files(root, *, provider="https://fixture-project.supabase.co", backend="http://localhost:8000"):
    (root / ".env.local").write_text(
        f"NEXT_PUBLIC_SUPABASE_URL={provider}\nNEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_fixture\nML_API_URL={backend}\nSUPABASE_SERVICE_ROLE_KEY=e2e-dummy-service-role-key\n",
        encoding="utf-8",
    )
    (root / ".env.test").write_text(f"E2E_EMAIL='{EMAIL}'\nE2E_PASSWORD='{PASSWORD}'\n", encoding="utf-8")


class Response(BytesIO):
    status = 200


class Opener:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == smoke.TIMEOUT_SECONDS
        if self.error:
            raise self.error
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, bytes):
            return Response(response)
        return Response(json.dumps(response).encode())


def successful_opener():
    return Opener([
        {"access_token": TOKEN, "refresh_token": "private-refresh", "user": {"id": OWNER, "email": EMAIL}},
        {"models": [{"model_id": "private-model", "owner": OWNER}], "token": TOKEN},
        {"races": [{"private": OWNER}, {"private": PASSWORD}], "count": 999999999},
        {"profiles": [{"id": OWNER, "email": EMAIL}], "private": PASSWORD},
    ])


def test_no_opt_in_no_network_no_secret_file_reads(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not read settings without explicit opt-in")
    monkeypatch.setattr(smoke, "configuration", forbidden)
    opener = Opener()
    result = smoke.run_smoke(authenticate_requested=False, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == "NOT_RUN"
    assert opener.requests == []
    assert result["all_five_features_completed"] is False


@pytest.mark.parametrize("value", ["https://fixture.supabase.co", "https://fixture-project.supabase.co/", "http://127.0.0.1:54321", "https://127.0.0.1:54321"])
def test_credential_destination_allowlist(value):
    assert smoke.origin(value, auth=True) == value.rstrip("/")


@pytest.mark.parametrize("value", [
    "http://fixture.supabase.co", "https://supabase.co", "https://fixture.supabase.co.evil.test",
    "https://supabase-co.evil.test", "https://fixture.supabase.co:444", "https://evil.test",
    "http://localhost:54321", "http://127.0.0.2:54321", "http://192.168.1.2:54321",
    "https://account:password@fixture.supabase.co", "https://fixture.supabase.co?token=secret",
    "https://fixture.supabase.co/auth/v1/token", "https://fixture.supabase.co#private",
    "http://127.0.0.1:0", "http://127.0.0.1:bad",
])
def test_disallowed_credential_targets_never_echo_value(value):
    with pytest.raises(smoke.SafeSmokeError) as exc:
        smoke.origin(value, auth=True)
    assert str(exc.value) == "CONFIGURATION_REJECTED"
    assert "secret" not in str(exc.value) and "password" not in str(exc.value)


@pytest.mark.parametrize("value", ["https://fixture.supabase.co", "http://localhost:3000", "http://127.0.0.2:3000", "http://0.0.0.0:3000"])
def test_app_origin_is_only_literal_127001(value):
    with pytest.raises(smoke.SafeSmokeError):
        smoke.origin(value)


def test_one_password_signin_then_exactly_three_safe_gets_redacted(tmp_path):
    env_files(tmp_path)
    opener = successful_opener()
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == 200 and result["admin_read_authorized"]
    assert [probe["count"] for probe in result["probes"]] == [1, 2, 1]
    assert result["capability_checked"] is False and result["all_five_features_completed"] is False
    assert len(opener.requests) == 4
    signin = opener.requests[0]
    assert signin.method == "POST" and signin.full_url == "https://fixture-project.supabase.co/auth/v1/token?grant_type=password"
    assert json.loads(signin.data) == {"email": EMAIL, "password": PASSWORD}
    assert signin.get_header("Apikey") == "sb_publishable_fixture"
    for request, endpoint in zip(opener.requests[1:], smoke.ALLOWED_ENDPOINTS):
        assert request.method == "GET" and request.data is None
        assert request.full_url == "http://127.0.0.1:3000" + endpoint
        assert request.get_header("Authorization") == "Bearer " + TOKEN
        assert not request.has_header("Cookie") and not request.has_header("Apikey")
    serialized = json.dumps(result)
    for value in (TOKEN, EMAIL, PASSWORD, OWNER, "private-refresh", "private-model", "fixture-project", "e2e-dummy-service-role-key"):
        assert value not in serialized
    assert "redacted" in repr(smoke.configuration(tmp_path))


def test_proxy_environment_ignored_and_redirects_disabled(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://fixture-secret-proxy.test")
    monkeypatch.setenv("HTTP_PROXY", "http://fixture-secret-proxy.test")
    opener = smoke.secure_opener()
    # urllib removes the ProxyHandler if it has no protocol methods: in either
    # representation no handler may retain an environment proxy.
    handlers = [handler for handler in opener.handlers if isinstance(handler, ProxyHandler)]
    assert all(handler.proxies == {} for handler in handlers)
    assert any(isinstance(handler, smoke.NoRedirect) for handler in opener.handlers)
    assert smoke.NoRedirect().redirect_request(None, None, 302, None, {}, "https://evil.test") is None


def test_auth_redirect_and_error_private_body_never_followed_or_read(tmp_path):
    env_files(tmp_path)
    body = BytesIO((EMAIL + PASSWORD + TOKEN).encode())
    error = HTTPError("https://fixture-project.supabase.co/auth/v1/token", 302, EMAIL, {"Location": "https://evil.test/" + TOKEN}, body)
    opener = Opener(error=error)
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == 302 and len(opener.requests) == 1
    assert result["admin_read_authorized"] is False
    assert EMAIL not in json.dumps(result) and TOKEN not in json.dumps(result)


def test_exception_details_cannot_escape(tmp_path):
    env_files(tmp_path)
    opener = Opener(error=URLError(EMAIL + PASSWORD + TOKEN + OWNER))
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == "TRANSPORT_FAILED"
    assert not any(secret in json.dumps(result) for secret in (EMAIL, PASSWORD, TOKEN, OWNER))


@pytest.mark.parametrize("endpoint", ["/api/performance", "/api/ml/train/capability", "/api/scrape/health", "/api/local-login", "/api/predict", "/api/ml/train/start", "/api/admin/profiles?extra=1"])
def test_every_other_endpoint_rejected_before_request(endpoint):
    opener = Opener()
    with pytest.raises(smoke.SafeSmokeError):
        smoke.probe(opener, "http://127.0.0.1:3000", endpoint, TOKEN)
    assert opener.requests == []


def test_body_size_and_invalid_json_fail_closed(tmp_path, monkeypatch):
    env_files(tmp_path)
    monkeypatch.setattr(smoke, "MAX_BODY_BYTES", 40)
    opener = Opener([b"x" * 41])
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == "RESPONSE_REJECTED" and len(opener.requests) == 1
    response = smoke.probe(Opener([b"invalid " + EMAIL.encode()]), "http://127.0.0.1:3000", smoke.ALLOWED_ENDPOINTS[0], TOKEN)
    assert response["status"] == "RESPONSE_REJECTED" and EMAIL not in json.dumps(response)


def test_expired_body_deadline_rejected():
    with pytest.raises(smoke.SafeSmokeError):
        smoke.bounded_json(Response(b"{}"), -1)


def test_remote_backend_stops_before_password_signin(tmp_path):
    env_files(tmp_path, backend="https://foreign-api.example.test")
    opener = successful_opener()
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == "CONFIGURATION_REJECTED" and opener.requests == []


def test_service_role_secret_not_usable_as_public_anon_key():
    encoded = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    for value in ("sb_secret_private", "header." + encoded + ".signature"):
        with pytest.raises(smoke.SafeSmokeError):
            smoke.public_anon_key(value)
    anon = base64.urlsafe_b64encode(json.dumps({"role": "anon"}).encode()).decode().rstrip("=")
    assert smoke.public_anon_key("header." + anon + ".signature")


def test_report_new_file_only_scoped_to_authorized_output(tmp_path):
    output = smoke.report_path(tmp_path, None)
    assert output.is_relative_to(tmp_path / "reports/generated/local-release-smoke")
    smoke.write_report(output, {"authentication_status": "NOT_RUN"})
    before = output.read_bytes()
    with pytest.raises(smoke.SafeSmokeError):
        smoke.report_path(tmp_path, output)
    with pytest.raises(FileExistsError):
        smoke.write_report(output, {"authentication_status": "changed"})
    assert output.read_bytes() == before
    with pytest.raises(smoke.SafeSmokeError):
        smoke.report_path(tmp_path, tmp_path / "outside.json")


def test_profiles_denial_does_not_infer_admin_from_jwt_claims(tmp_path):
    env_files(tmp_path)
    opener = Opener([
        {"access_token": TOKEN, "user": {"app_metadata": {"role": "admin"}, "id": OWNER}},
        {"models": []}, {"races": []},
        HTTPError("http://127.0.0.1:3000/api/admin/profiles", 403, OWNER, {}, BytesIO()),
    ])
    result = smoke.run_smoke(authenticate_requested=True, root=tmp_path, base="http://127.0.0.1:3000", opener=opener)
    assert result["authentication_status"] == 200
    assert result["probes"][2]["status"] == 403 and result["admin_read_authorized"] is False
    assert OWNER not in json.dumps(result)
