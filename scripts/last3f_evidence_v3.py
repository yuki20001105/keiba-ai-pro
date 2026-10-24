"""Versioned mobile-cache URL adapter for the frozen strict last-3F parser.

No production module is monkeypatched. A private parser module is loaded and
only its URL allowlist is extended for one public mobile result route. Mobile
sources additionally require original cache bytes and the exact cache receipt,
even when the publisher provides a canonical tag. Date/whole-runner/clock
validation remains the frozen parser's code; no historical-capture claim is made.
"""
from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE_PARSER_PATH = ROOT / "python-api/scraping/last3f_evidence.py"
BASE_PARSER_SHA256 = hashlib.sha256(BASE_PARSER_PATH.read_bytes()).hexdigest()
_spec = importlib.util.spec_from_file_location("_last3f_evidence_v3_private", BASE_PARSER_PATH)
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
_original_url_race_id = _parser._url_race_id


def _mobile_race_id(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "db.sp.netkeiba.com"
            or parsed.username or parsed.password or parsed.fragment or parsed.query
            or parsed.port not in (None, 443)):
        raise ValueError("last3f-v3-mobile-source-url-not-allowlisted")
    match = re.fullmatch(r"/race/(\d{12})/?", parsed.path)
    if not match:
        raise ValueError("last3f-v3-mobile-source-url-not-result-page")
    return match[1]


def _private_url_race_id(url):
    if urlsplit(url).hostname == "db.sp.netkeiba.com":
        return _mobile_race_id(url)
    return _original_url_race_id(url)


_parser._url_race_id = _private_url_race_id


def _direct_identity_receipt(receipt, *, source_url, observed_at, raw_sha256, raw_body):
    """Validate an actual direct response, not a synthesized cache timestamp."""
    if not isinstance(receipt, dict) or not isinstance(raw_body, bytes) or not raw_body:
        raise ValueError("last3f-v3-direct-receipt-and-original-bytes-required")
    if (receipt.get("schema") != "last3f-direct-http-receipt-v3"
            or receipt.get("request_url") != source_url or receipt.get("response_url") != source_url
            or receipt.get("response_status") != 200 or isinstance(receipt.get("response_status"), bool)
            or receipt.get("body_sha256") != raw_sha256
            or hashlib.sha256(raw_body).hexdigest() != raw_sha256
            or receipt.get("body_complete") is not True or receipt.get("attempts") != 1
            or isinstance(receipt.get("attempts"), bool) or receipt.get("source") != "network"):
        raise ValueError("last3f-v3-direct-transport-url-status-body-mismatch")
    _mobile_race_id(source_url)
    observed_at = _parser._observed_at(observed_at)
    received = _parser._observed_at(receipt.get("received_at_utc"))
    if datetime.fromisoformat(observed_at.replace("Z", "+00:00")) != datetime.fromisoformat(received.replace("Z", "+00:00")):
        raise ValueError("last3f-v3-direct-transport-observation-time-mismatch")
    return {**receipt, "raw_bytes_digest_verified": True}


# This second private parser replaces only the absent-canonical transport
# verifier. The supplied object is the real HTTP receipt (no fetched_at or
# fabricated cache row). The frozen date/all-runner/clock validation is reused.
_network_spec = importlib.util.spec_from_file_location("_last3f_evidence_v3_direct_private", BASE_PARSER_PATH)
_network_parser = importlib.util.module_from_spec(_network_spec)
_network_spec.loader.exec_module(_network_parser)
_network_parser._url_race_id = _private_url_race_id
_network_parser._cache_identity_receipt = _direct_identity_receipt


def parse_last3f_evidence_v3(html, **kwargs):
    """Same keyword arguments as parse_last3f_evidence, strict mobile opt-in.

    For desktop URLs this delegates without relaxing the existing policy. For
    mobile URLs it requires allow_cached_transport_identity=True, original
    raw_body bytes and exact normalized_url/status/body_sha256/fetched_at cache
    receipt. A cache receipt proves retrieval provenance, not pre-race capture.
    """
    direct = kwargs.pop("allow_direct_transport_identity", False)
    direct_receipt = kwargs.pop("transport_receipt", None)
    source_url = kwargs.get("source_url", "")
    mobile = urlsplit(source_url).hostname == "db.sp.netkeiba.com"
    if direct is not False and direct is not True:
        raise ValueError("last3f-v3-direct-identity-explicit-boolean-required")
    if direct is True:
        if not mobile or kwargs.get("allow_cached_transport_identity") or kwargs.get("cache_receipt") is not None:
            raise ValueError("last3f-v3-direct-cache-provenance-must-not-be-mixed")
        verified = _direct_identity_receipt(direct_receipt, source_url=source_url,
            observed_at=kwargs.get("observed_at_utc", ""), raw_sha256=kwargs.get("raw_body_sha256"),
            raw_body=kwargs.get("raw_body"))
        evidence = _network_parser.parse_last3f_evidence(html, **{
            **kwargs, "allow_cached_transport_identity": True, "cache_receipt": direct_receipt})
        evidence["schema"] = "last3f-source-evidence-v3"
        source = evidence["source"]
        source.pop("cached_transport_receipt", None)
        source.update({"network_transport_receipt": verified,
            "identity_grade": "mobile_network_transport_url_plus_visible_date_and_all_runners",
            "base_parser_sha256": BASE_PARSER_SHA256,
            "url_policy_version": "public-mobile-direct-exact-route-v3",
            "historical_capture_time_proven": False})
        return evidence
    if direct_receipt is not None:
        raise ValueError("last3f-v3-direct-identity-opt-in-required")
    if not mobile:
        return _parser.parse_last3f_evidence(html, **kwargs)
    _mobile_race_id(source_url)
    if kwargs.get("allow_cached_transport_identity") is not True:
        raise ValueError("last3f-v3-mobile-original-cache-receipt-required")
    receipt = _parser._cache_identity_receipt(kwargs.get("cache_receipt"),
        source_url=source_url, observed_at=kwargs.get("observed_at_utc", ""),
        raw_sha256=kwargs.get("raw_body_sha256"), raw_body=kwargs.get("raw_body"))
    evidence = _parser.parse_last3f_evidence(html, **kwargs)
    evidence["schema"] = "last3f-source-evidence-v3"
    source = evidence["source"]
    source["base_parser_sha256"] = BASE_PARSER_SHA256
    source["url_policy_version"] = "public-mobile-cache-exact-route-v3"
    source["identity_grade"] = "mobile_cached_transport_url_plus_visible_date_and_all_runners"
    source["cached_transport_receipt"] = receipt
    source["historical_capture_time_proven"] = False
    return evidence
