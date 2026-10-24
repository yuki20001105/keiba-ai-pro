"""Isolated last-3F HTTP evidence adapter; never writes a race database.

The production fetcher, provider admission controller and source parser are
reused unchanged. This adapter adds immutable response diagnostics and a
batch-local, non-resetting circuit breaker. It does not retry, change hosts,
rotate request headers, authenticate, or grant legacy-cache identity fallback.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import uuid
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "python-api") not in sys.path:
    sys.path.insert(0, str(ROOT / "python-api"))

from scraping.last3f_evidence import parse_last3f_evidence

SCHEMA = "last3f-http-observation-v3"
MAX_BODY_BYTES = 3_000_000
HEADER_ALLOWLIST = frozenset({"content-type", "content-length", "content-encoding",
    "date", "server", "retry-after", "x-cache", "x-request-id", "cf-ray"})


def _now():
    return datetime.now(timezone.utc).isoformat()


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def _exclusive(path, body):
    with Path(path).open("xb") as stream:
        stream.write(body)


def _safe_headers(headers):
    """No cookies, authorization, redirect tokens or arbitrary header fields."""
    result = {}
    for name, value in headers.items():
        key = str(name).lower()
        if key in HEADER_ALLOWLIST:
            text = re.sub(r"[\x00-\x1f\x7f]", "", str(value))[:512]
            result[key] = ("[REDACTED]" if re.search(
                r"(?i)(?:bearer\s|password|(?:session|token|cookie|authorization)\s*[:=])", text) else text)
    return result


def _canonical_url(value):
    parsed = urlsplit(str(value))
    if parsed.hostname == "db.sp.netkeiba.com":
        from last3f_evidence_v3 import _mobile_race_id
        rid = _mobile_race_id(str(value))
        return f"https://db.sp.netkeiba.com/race/{rid}/"
    query = parse_qs(parsed.query, keep_blank_values=True)
    ids = query.get("race_id", [])
    if (parsed.scheme != "https" or parsed.hostname != "race.netkeiba.com"
            or parsed.path != "/race/result.html" or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443) or set(query) != {"race_id"}
            or len(ids) != 1 or not re.fullmatch(r"\d{12}", ids[0])):
        raise ValueError("last3f-v3-noncanonical-public-result-url")
    return f"https://race.netkeiba.com/race/result.html?race_id={ids[0]}"


class TransportHalt(RuntimeError):
    """No further HTTP may be sent by this client instance."""

    def __init__(self, reason, receipt=None):
        super().__init__(reason)
        self.reason, self.receipt = reason, receipt


class SourceRejected(ValueError):
    """No usable source was returned; callers must not apply any clock value."""

    def __init__(self, reason, receipt=None):
        super().__init__(reason)
        self.reason, self.receipt = reason, receipt


@dataclass(frozen=True)
class VerifiedSource:
    response: object
    html: str
    evidence: dict
    observed_at_utc: str
    receipt: dict
    receipt_file: str
    receipt_sha256: str


class _Observation:
    def __init__(self, response, limit):
        self.response, self.limit = response, limit
        self.received_at_utc = _now()
        self.body = bytearray()
        self.complete = self.truncated = False
        self.read_error = None
        self.stream = getattr(response, "content", None)

    def append(self, chunk):
        chunk = bytes(chunk)
        left = self.limit - len(self.body)
        self.body.extend(chunk[:max(0, left)])
        if len(chunk) > left:
            self.truncated = True

    async def finish(self):
        # Some fetcher paths return at response headers (401/403). Read only
        # within the same bounded request context, without another HTTP call.
        if self.complete or self.truncated:
            return
        try:
            if self.stream is not None and hasattr(self.stream, "iter_chunked"):
                async for chunk in self.stream.iter_chunked(65536):
                    self.append(chunk)
                    if self.truncated:
                        break
                else:
                    self.complete = True
            else:
                self.append(await self.response.read())
                self.complete = not self.truncated
        except Exception as exc:
            self.read_error = type(exc).__name__


class _Content:
    def __init__(self, observation):
        self.observation = observation

    async def iter_chunked(self, size):
        observation = self.observation
        async for chunk in observation.stream.iter_chunked(size):
            observation.append(chunk)
            yield chunk
            if observation.truncated:
                return
        observation.complete = True


class _Response:
    def __init__(self, observation):
        self.observation = observation
        if observation.stream is not None and hasattr(observation.stream, "iter_chunked"):
            self.content = _Content(observation)

    def __getattr__(self, name):
        return getattr(self.observation.response, name)

    async def read(self):
        body = bytes(await self.observation.response.read())
        self.observation.append(body)
        self.observation.complete = not self.observation.truncated
        return body


class _Context:
    def __init__(self, context, owner):
        self.context, self.owner = context, owner
        self.observation = None

    async def __aenter__(self):
        response = await self.context.__aenter__()
        self.observation = _Observation(response, self.owner.limit)
        self.owner.observations.append(self.observation)
        return _Response(self.observation)

    async def __aexit__(self, *args):
        try:
            if self.observation:
                await self.observation.finish()
        finally:
            result = await self.context.__aexit__(*args)
        return result


class _Session:
    def __init__(self, session, limit):
        self.session, self.limit = session, limit
        self.observations = []

    def get(self, url, **kwargs):
        return _Context(self.session.get(url, **kwargs), self)


class Last3FTransportV3:
    """One serial attempt per target, strict source verification, no DB access.

    ``fetch(session, target)`` returns VerifiedSource. Target needs race_id,
    race_date and expected_runners, identical to the frozen V2 repair plan.
    TransportHalt is terminal for this instance and must end the batch.
    SourceRejected is non-applicable evidence; callers may journal it as pending
    but must never fabricate zero clocks or mark the target complete.
    """

    def __init__(self, output_dir, *, http400_threshold=1, error_threshold=3,
                 max_body_bytes=MAX_BODY_BYTES, source_policy="current-result",
                 min_interval_sec=3.0, fetcher=None):
        if (isinstance(http400_threshold, bool) or not isinstance(http400_threshold, int)
                or not 1 <= http400_threshold <= 3 or isinstance(error_threshold, bool)
                or not isinstance(error_threshold, int) or not 1 <= error_threshold <= 3
                or isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int)
                or not 1 <= max_body_bytes <= MAX_BODY_BYTES):
            raise ValueError("last3f-v3-bounded-policy-required")
        if source_policy not in {"current-result", "archive-mobile"}:
            raise ValueError("last3f-v3-fixed-source-policy-required")
        if (isinstance(min_interval_sec, bool) or not isinstance(min_interval_sec, (int, float))
                or not math.isfinite(min_interval_sec) or min_interval_sec < 3.0):
            raise ValueError("last3f-v3-minimum-three-second-shared-interval-required")
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.http400_threshold, self.error_threshold = http400_threshold, error_threshold
        self.max_body_bytes, self.fetcher = max_body_bytes, fetcher
        self._source_policy = source_policy
        self._min_interval_sec = float(min_interval_sec)
        self.consecutive400 = self.consecutive_errors = 0
        self.halt_reason = None
        self.last_receipt = None
        self.lock = asyncio.Lock()

    @property
    def source_policy(self):
        """Chosen before the first attempt; there is no error-triggered fallback."""
        return self._source_policy

    @property
    def min_interval_sec(self):
        return self._min_interval_sec

    def _seal(self, target, url, session, response, error=None):
        observations = session.observations
        # Retry/redirect policies are fixed to zero/off; extra responses cannot
        # silently become a new source. Preserve all observations for diagnosis.
        saved = []
        for item in observations:
            body = bytes(item.body)
            prefix = str(target["race_id"]) + "-" + uuid.uuid4().hex
            body_path = self.output_dir / (prefix + ".body.bin")
            _exclusive(body_path, body)
            final_url = str(getattr(item.response, "url", url))
            try:
                _canonical_url(final_url)
                final_url_allowlisted = True
            except ValueError:
                # Never persist credentials or unknown query tokens.
                final_url, final_url_allowlisted = "[REDACTED_UNALLOWLISTED_URL]", False
            saved.append({"body_file": str(body_path), "captured_body_bytes": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_digest_scope": "complete_decompressed_response" if item.complete and not item.truncated else "captured_prefix_only",
                "body_complete": item.complete and not item.truncated,
                "body_truncated": item.truncated, "body_read_error": item.read_error,
                "http_status": int(item.response.status), "final_url": final_url,
                "final_url_allowlisted": final_url_allowlisted,
                "headers": _safe_headers(item.response.headers),
                "received_at_utc": item.received_at_utc})
        receipt = {"schema": SCHEMA, "race_id": target["race_id"], "expected_race_date": target["race_date"],
            "request_url": url, "recorded_at_utc": _now(), "wire_response_observed": bool(saved),
            "http_status": getattr(response, "status", 0), "source": getattr(response, "source", "exception"),
            "attempts": getattr(response, "attempts", 0), "observations": saved,
            "error_class": error, "automatic_retries": 0, "redirects_allowed": False,
            "source_policy": self.source_policy,
            "request_headers_recorded": False, "historical_capture_time_proven": False,
            "minimum_shared_interval_seconds": self.min_interval_sec, "identity_validation": "not_yet_performed"}
        return receipt

    def _save_receipt(self, receipt):
        path = self.output_dir / (receipt["race_id"] + "-" + uuid.uuid4().hex + ".json")
        payload = _encoded(receipt)
        _exclusive(path, payload)
        self.last_receipt = {"receipt_file": str(path), "receipt_sha256": hashlib.sha256(payload).hexdigest(),
                             "observation": receipt}
        return self.last_receipt

    def _halt(self, reason, receipt):
        self.halt_reason = reason
        _exclusive(self.output_dir / ("circuit-open-" + uuid.uuid4().hex + ".json"),
                   _encoded({"schema": "last3f-http-circuit-v3", "recorded_at_utc": _now(),
                             "reason": reason, "automatic_reset": False, "receipt": receipt}))
        raise TransportHalt(reason, receipt)

    async def fetch(self, session, target):
        async with self.lock:
            if self.halt_reason:
                raise TransportHalt(self.halt_reason, self.last_receipt)
            race_id = str(target.get("race_id", ""))
            if (not re.fullmatch(r"\d{12}", race_id) or not 1 <= int(race_id[4:6]) <= 10
                    or not target.get("race_date") or not target.get("expected_runners")):
                raise ValueError("last3f-v3-target-plan-identity-required")
            url = _canonical_url(f"https://db.sp.netkeiba.com/race/{race_id}/" if self.source_policy == "archive-mobile"
                                 else f"https://race.netkeiba.com/race/result.html?race_id={race_id}")
            shim = _Session(session, self.max_body_bytes)
            if self.fetcher is None:
                from scraping.fetch_pipeline import fetch_bytes
                fetcher = fetch_bytes
            else:
                fetcher = self.fetcher
            response = None
            try:
                response = await fetcher(shim, url, use_cache=False, force_refresh=True,
                    max_retries=0, retry_statuses=set(), allow_redirects=False,
                    min_interval_sec=self.min_interval_sec, max_body_bytes=self.max_body_bytes, total_timeout_sec=30)
            except BaseException as exc:
                receipt = self._seal(target, url, shim, response, type(exc).__name__)
                self._save_receipt(receipt)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self._halt("transport-exception-needs-review", self.last_receipt)
            receipt = self._seal(target, url, shim, response)
            status = response.status
            self.consecutive400 = self.consecutive400 + 1 if status == 400 else 0
            if status != 200 or response.source == "access-blocked" or not response.body:
                self.consecutive_errors += 1
            if status in {401, 403, 429} or response.source == "access-blocked":
                saved = self._save_receipt(receipt)
                self._halt("provider-access-or-rate-restriction", saved)
            if (len(shim.observations) != 1 or response.attempts != 1
                    or not receipt["observations"][0]["final_url_allowlisted"]):
                self._save_receipt(receipt)
                self._halt("unverified-or-multiple-response-transport", self.last_receipt)
            if status != 200 or response.source == "access-blocked" or not response.body:
                saved = self._save_receipt(receipt)
                if self.consecutive400 >= self.http400_threshold:
                    self._halt("http400-circuit-open", saved)
                if self.consecutive_errors >= self.error_threshold:
                    self._halt("consecutive-http-errors-circuit-open", saved)
                raise SourceRejected(f"http-{status}-source-not-applicable", saved)
            observation = receipt["observations"][0]
            if (not observation["body_complete"] or observation["body_truncated"]
                    or hashlib.sha256(response.body).hexdigest() != observation["body_sha256"]
                    or observation["final_url"] != url or observation["http_status"] != 200):
                self._save_receipt(receipt)
                self._halt("response-body-or-transport-lineage-mismatch", self.last_receipt)
            from scraping.fetch_pipeline import decode_html_body
            html = decode_html_body(response.body)
            observed = observation["received_at_utc"]
            try:
                arguments = dict(race_id=race_id, source_url=url,
                    observed_at_utc=observed, raw_body_sha256=observation["body_sha256"],
                    expected_date=target["race_date"], expected_runners=target["expected_runners"],
                    http_status=status, raw_body=response.body)
                if self.source_policy == "archive-mobile":
                    from last3f_evidence_v3 import parse_last3f_evidence_v3
                    direct_receipt = {"schema": "last3f-direct-http-receipt-v3", "request_url": url,
                        "response_url": observation["final_url"], "response_status": observation["http_status"],
                        "body_sha256": observation["body_sha256"], "received_at_utc": observed,
                        "body_complete": True, "attempts": response.attempts, "source": response.source}
                    evidence = parse_last3f_evidence_v3(html, **arguments,
                        allow_direct_transport_identity=True, transport_receipt=direct_receipt)
                else:
                    evidence = parse_last3f_evidence(html, **arguments)
            except (ValueError, TypeError, KeyError) as exc:
                receipt["identity_validation"] = "rejected"
                receipt["identity_rejection_reason"] = str(exc)[:300]
                saved = self._save_receipt(receipt)
                self.consecutive_errors += 1
                if self.consecutive_errors >= self.error_threshold:
                    self._halt("consecutive-source-rejections-circuit-open", saved)
                raise SourceRejected(str(exc), saved) from exc
            self.consecutive_errors = 0
            receipt["identity_validation"] = evidence["source"]["identity_grade"]
            saved = self._save_receipt(receipt)
            return VerifiedSource(response, html, evidence, observed, receipt,
                                  saved["receipt_file"], saved["receipt_sha256"])
