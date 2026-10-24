"""Synthetic-only transport circuit/diagnostics and mobile-cache identity tests."""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from last3f_http_transport_v3 import Last3FTransportV3, SourceRejected, TransportHalt
from last3f_evidence_v3 import parse_last3f_evidence_v3

RID = "202101010101"
URL = f"https://race.netkeiba.com/race/result.html?race_id={RID}"
MOBILE = f"https://db.sp.netkeiba.com/race/{RID}/"
RUNNERS = [{"horse_number": 1, "horse_id": "2018100001", "horse_name": "テストワン"},
           {"horse_number": 2, "horse_id": "2018100002", "horse_name": "テストツー"}]
TARGET = {"race_id": RID, "race_date": "20210612", "expected_runners": RUNNERS}


def document(values=("34.8", "-"), canonical=URL, date="2021年6月12日"):
    canonical_tag = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f'''<html><head><meta charset="utf-8"><title>結果 | {date} 札幌1R</title>{canonical_tag}</head>
<body><div class="RaceData02">1回 札幌 1日目 2頭</div><table class="ResultsByRaceDetail">
<tr><th>着順</th><th>馬番</th><th>馬名</th><th>後3F</th></tr>
<tr><td>1</td><td>1</td><td><a href="/horse/2018100001/">テストワン</a></td><td>{values[0]}</td></tr>
<tr><td>2</td><td>2</td><td><a href="/horse/2018100002/">テストツー</a></td><td>{values[1]}</td></tr>
</table></body></html>'''


class Content:
    def __init__(self, body):
        self.body, self.position = body, 0

    async def iter_chunked(self, size):
        while self.position < len(self.body):
            chunk = self.body[self.position:self.position + size]
            self.position += len(chunk)
            yield chunk


class Response:
    def __init__(self, body, status=200, headers=None, url=URL):
        self.content = Content(body)
        self.status, self.headers, self.url = status, headers or {}, url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)


async def fake_fetcher(session, url, **kwargs):
    assert kwargs == {"use_cache": False, "force_refresh": True, "max_retries": 0,
        "retry_statuses": set(), "allow_redirects": False, "min_interval_sec": kwargs["min_interval_sec"],
        "max_body_bytes": kwargs["max_body_bytes"], "total_timeout_sec": 30}
    assert kwargs["min_interval_sec"] >= 3.0
    async with session.get(url, allow_redirects=False) as response:
        # Replicate early header-only refusal in the frozen provider pipeline.
        if response.status in {401, 403}:
            return SimpleNamespace(status=response.status, body=b"", source="access-blocked",
                attempts=1, response_headers={}, url=url)
        chunks = []
        async for chunk in response.content.iter_chunked(100):
            chunks.append(chunk)
            if sum(map(len, chunks)) > kwargs["max_body_bytes"]:
                return SimpleNamespace(status=0, body=b"", source="network-rejected",
                    attempts=1, response_headers={}, url=url)
        return SimpleNamespace(status=response.status, body=b"".join(chunks), source="network",
            attempts=1, response_headers=response.headers, url=url)


def client(tmp_path, **kwargs):
    return Last3FTransportV3(tmp_path, fetcher=fake_fetcher, **kwargs)


def test_first_http400_seals_body_and_headers_then_permanently_stops(tmp_path):
    body = b"<h1>400 Bad Request</h1>"
    session = Session([Response(body, 400, {"Server": "proxy", "Set-Cookie": "secret",
        "Authorization": "Bearer secret", "X-Request-ID": "id-123", "Date": "test"})])
    transport = client(tmp_path)
    with pytest.raises(TransportHalt, match="http400-circuit-open") as captured:
        asyncio.run(transport.fetch(session, TARGET))
    receipt = captured.value.receipt
    saved = json.loads(Path(receipt["receipt_file"]).read_text(encoding="utf-8"))
    observed = saved["observations"][0]
    assert Path(observed["body_file"]).read_bytes() == body
    assert observed["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert observed["body_complete"] and observed["body_digest_scope"] == "complete_decompressed_response"
    assert observed["headers"] == {"server": "proxy", "x-request-id": "id-123", "date": "test"}
    assert "secret" not in Path(receipt["receipt_file"]).read_text(encoding="utf-8")
    assert saved["historical_capture_time_proven"] is False
    assert saved["automatic_retries"] == 0
    with pytest.raises(TransportHalt):
        asyncio.run(transport.fetch(session, TARGET))
    assert len(session.requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_restriction_stops_and_early_header_return_still_saves_body(tmp_path, status):
    body = b"<h1>restricted</h1>"
    transport = client(tmp_path)
    session = Session([Response(body, status)])
    with pytest.raises(TransportHalt, match="provider-access-or-rate-restriction") as captured:
        asyncio.run(transport.fetch(session, TARGET))
    observed = captured.value.receipt["observation"]["observations"][0]
    assert Path(observed["body_file"]).read_bytes() == body
    assert observed["body_complete"]


def test_three_http400_limit_is_bounded_and_no_requests_after_open(tmp_path):
    transport = client(tmp_path, http400_threshold=3)
    session = Session([Response(b"bad", 400) for _ in range(3)])
    for _ in range(2):
        with pytest.raises(SourceRejected, match="http-400"):
            asyncio.run(transport.fetch(session, TARGET))
    with pytest.raises(TransportHalt, match="http400-circuit-open"):
        asyncio.run(transport.fetch(session, TARGET))
    assert len(session.requests) == 3
    assert len(list(tmp_path.glob("*.body.bin"))) == 3


@pytest.mark.parametrize("values,states", [(("34.8", "-"), ("valid", "missing")),
    (("**", "0"), ("masked", "invalid"))])
def test_success_requires_full_identity_and_does_not_zero_fill(tmp_path, values, states):
    body = document(values).encode()
    got = asyncio.run(client(tmp_path).fetch(Session([Response(body)]), TARGET))
    assert [item["last3f_state"] for item in got.evidence["rows"]] == list(states)
    assert all(item["last3f_seconds"] is None for item in got.evidence["rows"] if item["last3f_state"] != "valid")
    assert got.evidence["source"]["identity_grade"] == "canonical_url_plus_visible_date_and_all_runners"
    assert got.evidence["source"]["historical_capture_time_proven"] is False
    assert got.receipt["identity_validation"] == got.evidence["source"]["identity_grade"]


@pytest.mark.parametrize("html", [document(date="2021年6月13日"), document().replace("テストツー", "別馬"),
    document(canonical=URL.replace(RID, "202101010102")), document(canonical=None)])
def test_wrong_date_any_runner_or_missing_canonical_rejects_entire_source(tmp_path, html):
    with pytest.raises(SourceRejected) as captured:
        asyncio.run(client(tmp_path).fetch(Session([Response(html.encode())]), TARGET))
    assert captured.value.receipt["observation"]["identity_validation"] == "rejected"


def test_oversize_body_records_prefix_digest_not_full_response_digest(tmp_path):
    transport = client(tmp_path, max_body_bytes=30, error_threshold=1)
    with pytest.raises(TransportHalt) as captured:
        asyncio.run(transport.fetch(Session([Response(b"x" * 100)]), TARGET))
    observed = captured.value.receipt["observation"]["observations"][0]
    assert len(Path(observed["body_file"]).read_bytes()) == 30
    assert observed["body_truncated"] and not observed["body_complete"]
    assert observed["body_digest_scope"] == "captured_prefix_only"


def test_repeated_200_wrong_identity_halts_instead_of_scanning_the_whole_plan(tmp_path):
    bad = document().replace("テストツー", "別馬").encode()
    transport = client(tmp_path)
    session = Session([Response(bad) for _ in range(3)])
    for _ in range(2):
        with pytest.raises(SourceRejected):
            asyncio.run(transport.fetch(session, TARGET))
    with pytest.raises(TransportHalt, match="consecutive-source-rejections-circuit-open"):
        asyncio.run(transport.fetch(session, TARGET))
    assert len(session.requests) == 3


def test_repeated_200_empty_response_is_not_mistaken_for_success(tmp_path):
    transport = client(tmp_path)
    session = Session([Response(b"") for _ in range(3)])
    for _ in range(2):
        with pytest.raises(SourceRejected):
            asyncio.run(transport.fetch(session, TARGET))
    with pytest.raises(TransportHalt, match="consecutive-http-errors-circuit-open"):
        asyncio.run(transport.fetch(session, TARGET))


def test_lifecycle_cancel_is_not_swallowed_by_response_context(tmp_path):
    async def cancelling_fetcher(session, url, **kwargs):
        async with session.get(url, allow_redirects=False):
            raise asyncio.CancelledError()
    transport = Last3FTransportV3(tmp_path, fetcher=cancelling_fetcher)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(transport.fetch(Session([Response(b"diagnostic")]), TARGET))
    assert len(list(tmp_path.glob("*.body.bin"))) == 1


def test_shared_provider_refusal_without_a_wire_response_does_not_claim_one(tmp_path):
    async def blocked_fetcher(session, url, **kwargs):
        return SimpleNamespace(status=403, body=b"", source="access-blocked", attempts=0)
    transport = Last3FTransportV3(tmp_path, fetcher=blocked_fetcher)
    session = Session([])
    with pytest.raises(TransportHalt, match="provider-access-or-rate-restriction") as captured:
        asyncio.run(transport.fetch(session, TARGET))
    receipt = captured.value.receipt["observation"]
    assert receipt["wire_response_observed"] is False and receipt["observations"] == []
    assert session.requests == []


@pytest.mark.parametrize("kwargs", [{"http400_threshold": 0}, {"http400_threshold": 4},
    {"http400_threshold": True}, {"error_threshold": 4}, {"error_threshold": True},
    {"max_body_bytes": 3_000_001}, {"max_body_bytes": 0}])
def test_unbounded_policy_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        client(tmp_path, **kwargs)


def mobile_arguments(html, **patch):
    body = html.encode()
    observed = "2026-10-17T10:00:00+00:00"
    sha = hashlib.sha256(body).hexdigest()
    values = {"race_id": RID, "source_url": MOBILE, "observed_at_utc": observed,
        "raw_body_sha256": sha, "expected_date": "20210612", "expected_runners": deepcopy(RUNNERS),
        "raw_body": body, "http_status": 200, "allow_cached_transport_identity": True,
        "cache_receipt": {"normalized_url": MOBILE, "status": 200, "body_sha256": sha,
                          "fetched_at": datetime.fromisoformat(observed).timestamp()}}
    values.update(patch)
    return values


def test_mobile_cache_preserves_actual_url_receipt_and_strict_identity():
    html = document(canonical=None)
    got = parse_last3f_evidence_v3(html, **mobile_arguments(html))
    assert got["source"]["source_url"] == MOBILE
    assert got["source"]["identity_grade"] == "mobile_cached_transport_url_plus_visible_date_and_all_runners"
    assert got["source"]["cached_transport_receipt"]["normalized_url"] == MOBILE
    assert got["source"]["cached_transport_receipt"]["raw_bytes_digest_verified"]
    assert not got["source"]["historical_capture_time_proven"]
    assert len(got["source"]["base_parser_sha256"]) == 64


@pytest.mark.parametrize("patch", [{"allow_cached_transport_identity": False}, {"raw_body": None},
    {"cache_receipt": None}, {"raw_body_sha256": "0" * 64}, {"expected_date": "20210613"},
    {"expected_runners": RUNNERS[:1]}, {"source_url": MOBILE + "?cookie=secret"},
    {"source_url": MOBILE.replace("https", "http")}, {"http_status": 400}])
def test_mobile_has_no_implicit_transport_identity_or_temporal_relaxation(patch):
    html = document(canonical=None)
    with pytest.raises((ValueError, TypeError)):
        parse_last3f_evidence_v3(html, **mobile_arguments(html, **patch))


def test_mobile_original_timestamp_cannot_be_replaced_by_now_or_race_date():
    html = document(canonical=None)
    args = mobile_arguments(html)
    args["cache_receipt"]["fetched_at"] = 123
    with pytest.raises(ValueError, match="observation-time-mismatch"):
        parse_last3f_evidence_v3(html, **args)


def test_mobile_present_conflicting_canonical_still_rejects():
    html = document(canonical=URL.replace(RID, "202101010102"))
    with pytest.raises(ValueError, match="canonical-race-id-mismatch"):
        parse_last3f_evidence_v3(html, **mobile_arguments(html))


def test_original_parser_remains_unmodified_and_rejects_mobile():
    from scraping.last3f_evidence import parse_last3f_evidence
    html = document(canonical=None)
    with pytest.raises(ValueError, match="source-url-not-allowlisted"):
        parse_last3f_evidence(html, **mobile_arguments(html))


def direct_arguments(html, **patch):
    values = mobile_arguments(html)
    values.pop("allow_cached_transport_identity")
    values.pop("cache_receipt")
    values.update(allow_direct_transport_identity=True, transport_receipt={
        "schema": "last3f-direct-http-receipt-v3", "request_url": MOBILE, "response_url": MOBILE,
        "response_status": 200, "body_sha256": values["raw_body_sha256"],
        "received_at_utc": values["observed_at_utc"], "body_complete": True,
        "attempts": 1, "source": "network"})
    values.update(patch)
    return values


def test_mobile_direct_response_has_real_http_receipt_not_a_cache_claim():
    html = document(canonical=None)
    result = parse_last3f_evidence_v3(html, **direct_arguments(html))
    source = result["source"]
    assert source["identity_grade"] == "mobile_network_transport_url_plus_visible_date_and_all_runners"
    assert source["source_url"] == MOBILE
    assert "cached_transport_receipt" not in source
    assert "fetched_at" not in source["network_transport_receipt"]
    assert source["network_transport_receipt"]["received_at_utc"] == "2026-10-17T10:00:00+00:00"
    assert source["network_transport_receipt"]["raw_bytes_digest_verified"] is True
    assert source["canonical_identity_verified"] is False
    assert source["historical_capture_time_proven"] is False


@pytest.mark.parametrize("field,value", [("schema", "cache"), ("request_url", URL),
    ("response_url", MOBILE + "?token=secret"), ("response_status", 400),
    ("body_sha256", "0" * 64), ("body_complete", False), ("attempts", 2),
    ("attempts", True), ("source", "cache"), ("received_at_utc", "2026-10-17T10:00:01+00:00")])
def test_direct_transport_receipt_exact_url_status_hash_time_and_one_attempt_required(field, value):
    html = document(canonical=None)
    args = direct_arguments(html)
    args["transport_receipt"][field] = value
    with pytest.raises(ValueError):
        parse_last3f_evidence_v3(html, **args)


def test_direct_and_cache_provenance_cannot_be_mixed():
    html = document(canonical=None)
    args = direct_arguments(html)
    args["cache_receipt"] = mobile_arguments(html)["cache_receipt"]
    with pytest.raises(ValueError, match="provenance-must-not-be-mixed"):
        parse_last3f_evidence_v3(html, **args)


@pytest.mark.parametrize("html", [document(date="2021年6月13日", canonical=None),
    document(canonical=None).replace("テストツー", "違う馬"),
    document(canonical=URL.replace(RID, "202101010102"))])
def test_direct_transport_never_relaxes_date_all_runners_or_contradictory_canonical(html):
    with pytest.raises(ValueError):
        parse_last3f_evidence_v3(html, **direct_arguments(html))


def test_preselected_archive_mobile_transport_uses_only_that_route(tmp_path):
    body = document(canonical=None).encode()
    session = Session([Response(body, url=MOBILE)])
    transport = client(tmp_path, source_policy="archive-mobile")
    got = asyncio.run(transport.fetch(session, TARGET))
    assert session.requests[0][0] == MOBILE
    assert got.evidence["source"]["identity_grade"] == "mobile_network_transport_url_plus_visible_date_and_all_runners"
    assert "cached_transport_receipt" not in got.evidence["source"]
    assert got.receipt["source_policy"] == "archive-mobile"
    with pytest.raises(AttributeError):
        transport.source_policy = "current-result"


def test_current_result_http400_never_triggers_mobile_fallback(tmp_path):
    session = Session([Response(b"bad", status=400)])
    transport = client(tmp_path)
    with pytest.raises(TransportHalt):
        asyncio.run(transport.fetch(session, TARGET))
    assert [item[0] for item in session.requests] == [URL]


def test_unallowlisted_source_policy_fails_before_network(tmp_path):
    with pytest.raises(ValueError, match="fixed-source-policy-required"):
        client(tmp_path, source_policy="another-host")


@pytest.mark.parametrize("minimum", [0, 1, 2.999, float("nan"), float("inf"), True, "3"])
def test_short_unbounded_or_non_numeric_shared_interval_is_rejected_before_request(tmp_path, minimum):
    with pytest.raises(ValueError, match="minimum-three-second-shared-interval-required"):
        client(tmp_path, min_interval_sec=minimum)


@pytest.mark.parametrize("minimum", [None, 5.0])
def test_transport_passes_at_least_three_seconds_to_shared_pacer_and_seals_actual_policy(tmp_path, minimum):
    recorded = []
    async def observed_fetcher(session, url, **kwargs):
        recorded.append(kwargs["min_interval_sec"])
        return await fake_fetcher(session, url, **kwargs)
    options = {} if minimum is None else {"min_interval_sec": minimum}
    transport = Last3FTransportV3(tmp_path, fetcher=observed_fetcher, **options)
    got = asyncio.run(transport.fetch(Session([Response(document().encode())]), TARGET))
    expected = 3.0 if minimum is None else minimum
    assert recorded == [expected]
    assert got.receipt["minimum_shared_interval_seconds"] == expected
    with pytest.raises(AttributeError):
        transport.min_interval_sec = 1.0
