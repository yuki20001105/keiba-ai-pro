"""Bounded server-side orchestration for targeted-refetch live validation.

Only server-owned scripts and filesystem paths are used.  The dry-run planner is
executed first and its URL samples are validated before the live validator is
allowed to make any external request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLANNER_SCRIPT = PROJECT_ROOT / "scripts" / "plan_p0_targeted_refetch.py"
VALIDATOR_SCRIPT = PROJECT_ROOT / "scripts" / "validate_p0_targeted_refetch_live.py"
REPORTS_DIR = PROJECT_ROOT / "reports"
DATA_DIR = PROJECT_ROOT / "keiba" / "data"

PLANNER_TIMEOUT_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 90.0
MAX_STDOUT_BYTES = 128 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_PLANNER_REPORT_BYTES = 2 * 1024 * 1024
MAX_VALIDATION_REPORT_BYTES = 1 * 1024 * 1024
MAX_PLANNER_INPUT_BYTES = 32 * 1024 * 1024
USER_COOLDOWN_SECONDS = 60.0

_PLANNER_INPUT_FILENAMES = (
    "scrape_missingness_audit.json",
    "p0_scrape_repair_plan.json",
    "p0_cache_coverage_diagnosis.json",
)

_TARGETS = ("all", "race", "horse", "result", "pedigree", "odds")
_URL_TYPES = ("all", "race-result", "race-detail", "horse-detail", "pedigree")
_SAMPLE_URL_TYPES = ("result_page", "race_detail", "horse_detail", "pedigree")
_PLANNER_SAFETY_FLAGS = (
    "read_only",
    "no_db_write",
    "no_http_access",
    "no_scrape_execute",
    "no_upsert",
    "no_force_refresh_execute",
)
_VALIDATION_SAFETY_FLAGS = (
    "small_live_validation_only",
    "max_urls_limited",
    "no_db_write",
    "no_upsert",
    "no_repair_execute",
    "no_production_table_write",
    "no_force_refresh_execute",
    "no_bulk_refetch",
    "redirects_disabled",
    "bounded_response_body",
    "bounded_total_runtime",
)
_RAW_COUNT_FIELDS = (
    "attempted_url_count",
    "http_success_count",
    "http_error_count",
    "parse_success_count",
    "parse_failed_count",
    "would_fix_count",
    "would_not_fix_count",
    "no_downgrade_skip_count",
    "repairable_from_live_count",
    "excluded_schema_review_count",
    "excluded_domain_allowed_count",
    "excluded_metadata_repair_count",
    "excluded_cache_available_count",
    "excluded_unsafe_url_count",
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PATH_RE = re.compile(
    r"file://|[A-Za-z]:[\\/]|\\\\[A-Za-z0-9.$_-]+\\|"
    r"(^|[^A-Za-z0-9_])/[A-Za-z0-9._-]|(^|[^A-Za-z0-9_])(?:~|\.\.)[\\/]",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"^[A-Za-z0-9_():-]{1,120}$")


class LiveValidationRequest(BaseModel):
    """Strict public request contract; every field is required."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["all", "race", "horse", "result", "pedigree", "odds"]
    url_type: Literal["all", "race-result", "race-detail", "horse-detail", "pedigree"]
    max_urls: StrictInt = Field(ge=1, le=3)
    confirm_live_fetch: StrictBool

    @field_validator("confirm_live_fetch")
    @classmethod
    def require_explicit_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("explicit live fetch confirmation is required")
        return value


class LiveValidationSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    url_type: Literal["result_page", "race_detail", "horse_detail", "pedigree"]
    race_id: str | None
    horse_id: str | None
    http_status: int
    parse_status: Literal["http_error", "parse_success", "parse_failed"]
    missing_fields_before: list[str]
    fields_found_after: list[str]
    would_fix_columns: list[str]
    action: str
    reason: str
    recommended_next_action: str


class CircuitBreakerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: int
    cooldown_sec: float


class LiveValidationRateLimitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_urls: int
    max_supported_urls: int
    min_interval_sec: float
    max_retries: Literal[1]
    retry_base_sec: float
    retry_jitter_sec: float
    retry_after_enabled: Literal[False]
    max_retry_after_sec: float
    per_request_timeout_sec: float
    total_timeout_sec: float
    max_body_bytes: int
    circuit_breaker: CircuitBreakerPolicy
    parallelism: Literal[1]
    fetch_pipeline_used: Literal[True]


class LiveValidationSafetyFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    small_live_validation_only: Literal[True]
    max_urls_limited: Literal[True]
    no_db_write: Literal[True]
    no_upsert: Literal[True]
    no_repair_execute: Literal[True]
    no_production_table_write: Literal[True]
    no_force_refresh_execute: Literal[True]
    no_bulk_refetch: Literal[True]
    redirects_disabled: Literal[True]
    bounded_response_body: Literal[True]
    bounded_total_runtime: Literal[True]


class LiveValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["all", "race", "horse", "result", "pedigree", "odds"]
    url_type: Literal["all", "race-result", "race-detail", "horse-detail", "pedigree"]
    max_urls_applied: int
    attempted_url_count: int
    http_success_count: int
    http_error_count: int
    parse_success_count: int
    parse_error_count: int
    would_fix_count: int
    would_not_fix_count: int
    no_downgrade_count: int
    repairable_count: int
    excluded_schema_review_count: int
    excluded_domain_allowed_count: int
    excluded_metadata_repair_count: int
    excluded_cache_available_count: int
    elapsed_seconds: float
    estimated_full_refetch_runtime_seconds: float
    sample_results: list[LiveValidationSample]
    recommended_next_actions: list[str]
    rate_limit_policy: LiveValidationRateLimitPolicy
    safety_flags: LiveValidationSafetyFlags
    verdict: Literal["pass", "warn"]
    verdict_reason: Literal["small-live-validation"]


class LiveValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live_validation: Literal[True]
    bounded: Literal[True]
    external_http: Literal[True]
    read_only: Literal[True]
    execution_enabled: Literal[False]
    result: LiveValidationResult


class LiveValidationServiceError(Exception):
    """Safe, already-classified failure that may be exposed by the API."""

    def __init__(self, status_code: int, detail: str, *, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


class _OutputLimitExceeded(Exception):
    pass


CommandRunner = Callable[[tuple[str, ...], Path, float, str], Awaitable[None]]


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_finite_nonnegative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _is_safe_text(value: Any, *, max_length: int = 240, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or len(value) > max_length:
        return False
    if not allow_empty and not value.strip():
        return False
    if _CONTROL_RE.search(value) or _PATH_RE.search(value):
        return False
    return True


def _is_safe_id(value: Any, length: int) -> bool:
    return value is None or (isinstance(value, str) and bool(re.fullmatch(rf"\d{{{length}}}", value)))


def _allowed_netkeiba_url(value: Any, url_type: str) -> bool:
    if not isinstance(value, str) or url_type not in _SAMPLE_URL_TYPES:
        return False
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "db.netkeiba.com"
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            return False
    except (TypeError, ValueError):
        return False

    patterns = {
        "result_page": r"/race/\d{12}/",
        "race_detail": r"/race/\d{12}/",
        "horse_detail": r"/horse/result/\d{10}/",
        "pedigree": r"/horse/ped/\d{10}/",
    }
    return bool(re.fullmatch(patterns[url_type], parsed.path))


def _url_path_id(url: str, url_type: str) -> str:
    path = urlsplit(url).path
    if url_type in {"result_page", "race_detail"}:
        return path.split("/")[2]
    return path.split("/")[3]


def _safe_string_list(value: Any, *, max_items: int, max_length: int = 120) -> list[str] | None:
    if not isinstance(value, list) or len(value) > max_items:
        return None
    if any(not _is_safe_text(item, max_length=max_length) for item in value):
        return None
    return list(value)


def _validate_planner_sample(value: Any, bucket: str) -> None:
    if not isinstance(value, dict):
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    if value.get("url_type") != bucket or not _allowed_netkeiba_url(value.get("url"), bucket):
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    if not _is_safe_id(value.get("race_id"), 12) or not _is_safe_id(value.get("horse_id"), 10):
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    path_id = _url_path_id(value["url"], bucket)
    if bucket in {"result_page", "race_detail"}:
        if value.get("race_id") != path_id:
            raise LiveValidationServiceError(502, "live validation planner report is invalid")
    elif value.get("horse_id") != path_id or value.get("race_id") is not None:
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    for key, limit in (
        ("reason", 160),
        ("column", 160),
        ("priority", 80),
        ("source", 160),
        ("recommended_next_action", 240),
    ):
        if not _is_safe_text(value.get(key), max_length=limit):
            raise LiveValidationServiceError(502, "live validation planner report is invalid")


def _validate_planner_report(raw: dict[str, Any], request: LiveValidationRequest) -> None:
    if raw.get("target") != request.target:
        raise LiveValidationServiceError(502, "live validation planner report does not match the request")
    if raw.get("verdict") not in {"pass", "warn"} or raw.get("verdict_reason") != "targeted-refetch-dry-run":
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    if "url_candidates" in raw:
        # The validator also consumes this legacy field.  The fixed planner does
        # not emit it, so rejecting it prevents hidden URLs bypassing preflight.
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    if not _is_nonnegative_int(raw.get("unique_url_count")):
        raise LiveValidationServiceError(502, "live validation planner report is invalid")

    flags = raw.get("safety_flags")
    if not isinstance(flags, dict) or any(flags.get(key) is not True for key in _PLANNER_SAFETY_FLAGS):
        raise LiveValidationServiceError(502, "live validation planner safety checks failed")

    samples = raw.get("sample_urls")
    if not isinstance(samples, dict) or set(samples) != set(_SAMPLE_URL_TYPES):
        raise LiveValidationServiceError(502, "live validation planner report is invalid")
    for bucket in _SAMPLE_URL_TYPES:
        rows = samples.get(bucket)
        if not isinstance(rows, list) or len(rows) > request.max_urls:
            raise LiveValidationServiceError(502, "live validation planner report is invalid")
        for row in rows:
            _validate_planner_sample(row, bucket)


def _parse_validation_sample(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LiveValidationServiceError(502, "live validation result is invalid")
    url_type = raw.get("url_type")
    if url_type not in _SAMPLE_URL_TYPES or not _allowed_netkeiba_url(raw.get("url"), url_type):
        raise LiveValidationServiceError(502, "live validation result is invalid")
    if not _is_safe_id(raw.get("race_id"), 12) or not _is_safe_id(raw.get("horse_id"), 10):
        raise LiveValidationServiceError(502, "live validation result is invalid")
    path_id = _url_path_id(raw["url"], url_type)
    if url_type in {"result_page", "race_detail"}:
        if raw.get("race_id") != path_id:
            raise LiveValidationServiceError(502, "live validation result is invalid")
    elif raw.get("horse_id") != path_id or raw.get("race_id") is not None:
        raise LiveValidationServiceError(502, "live validation result is invalid")
    http_status = raw.get("http_status")
    if not isinstance(http_status, int) or isinstance(http_status, bool) or not 0 <= http_status <= 599:
        raise LiveValidationServiceError(502, "live validation result is invalid")
    if raw.get("parse_status") not in {"http_error", "parse_success", "parse_failed"}:
        raise LiveValidationServiceError(502, "live validation result is invalid")

    lists: dict[str, list[str]] = {}
    for key in ("missing_fields_before", "fields_found_after", "would_fix_columns"):
        parsed = _safe_string_list(raw.get(key), max_items=32)
        if (
            parsed is None
            or len(set(parsed)) != len(parsed)
            or any(not _FIELD_RE.fullmatch(item) for item in parsed)
        ):
            raise LiveValidationServiceError(502, "live validation result is invalid")
        lists[key] = parsed

    for key in ("action", "reason", "recommended_next_action"):
        if not _is_safe_text(raw.get(key), max_length=240):
            raise LiveValidationServiceError(502, "live validation result is invalid")

    parse_status = raw["parse_status"]
    action = raw["action"]
    http_success = 200 <= http_status < 300
    would_fix = bool(lists["would_fix_columns"])
    found_set = set(lists["fields_found_after"])
    if raw["reason"] == "consistency:race_without_horse_data":
        expected_would_fix = (
            ["(check)"]
            if "(check)" in set(lists["missing_fields_before"]) and "(check)" in found_set
            else []
        )
    else:
        expected_would_fix = [name for name in lists["missing_fields_before"] if name in found_set]
    if lists["would_fix_columns"] != expected_would_fix:
        raise LiveValidationServiceError(502, "live validation repair evidence is inconsistent")
    if (http_success and parse_status == "http_error") or (not http_success and parse_status != "http_error"):
        raise LiveValidationServiceError(502, "live validation result is invalid")
    if parse_status == "http_error":
        if action != "http_error" or would_fix or lists["fields_found_after"]:
            raise LiveValidationServiceError(502, "live validation result is invalid")
    elif parse_status == "parse_failed":
        if not action.startswith("parse_failed:") or would_fix or lists["fields_found_after"]:
            raise LiveValidationServiceError(502, "live validation result is invalid")
    else:
        if not lists["fields_found_after"] and not would_fix:
            raise LiveValidationServiceError(502, "live validation result is invalid")
        if (would_fix and action != "would-fix") or (not would_fix and action != "no-downgrade-skip"):
            raise LiveValidationServiceError(502, "live validation result is invalid")

    return {
        "url": raw["url"],
        "url_type": url_type,
        "race_id": raw.get("race_id"),
        "horse_id": raw.get("horse_id"),
        "http_status": http_status,
        "parse_status": parse_status,
        "missing_fields_before": lists["missing_fields_before"],
        "fields_found_after": lists["fields_found_after"],
        "would_fix_columns": lists["would_fix_columns"],
        "action": raw["action"],
        "reason": raw["reason"],
        "recommended_next_action": raw["recommended_next_action"],
    }


def _parse_rate_limit_policy(raw: Any, request: LiveValidationRequest) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("max_urls") != request.max_urls:
        raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")
    integer_bounds = {
        "max_supported_urls": (3, 10),
        "max_retries": (1, 1),
        "max_body_bytes": (1, 2 * 1024 * 1024),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")

    numeric_bounds = {
        "min_interval_sec": (1.0, 60.0),
        "retry_base_sec": (0.0, 30.0),
        "retry_jitter_sec": (0.0, 10.0),
        "max_retry_after_sec": (0.0, 10.0),
        "per_request_timeout_sec": (0.001, 15.0),
        "total_timeout_sec": (0.001, TOTAL_TIMEOUT_SECONDS),
    }
    for key, (minimum, maximum) in numeric_bounds.items():
        value = raw.get(key)
        if not _is_finite_nonnegative(value) or not minimum <= float(value) <= maximum:
            raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")

    circuit = raw.get("circuit_breaker")
    if not isinstance(circuit, dict):
        raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")
    threshold = circuit.get("threshold")
    cooldown = circuit.get("cooldown_sec")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or not 1 <= threshold <= 10:
        raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")
    if not _is_finite_nonnegative(cooldown) or float(cooldown) > 120.0:
        raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")
    if raw.get("retry_after_enabled") is not False or raw.get("parallelism") != 1 or raw.get("fetch_pipeline_used") is not True:
        raise LiveValidationServiceError(502, "live validation rate-limit policy is invalid")

    return {
        "max_urls": request.max_urls,
        "max_supported_urls": raw["max_supported_urls"],
        "min_interval_sec": float(raw["min_interval_sec"]),
        "max_retries": raw["max_retries"],
        "retry_base_sec": float(raw["retry_base_sec"]),
        "retry_jitter_sec": float(raw["retry_jitter_sec"]),
        "retry_after_enabled": False,
        "max_retry_after_sec": float(raw["max_retry_after_sec"]),
        "per_request_timeout_sec": float(raw["per_request_timeout_sec"]),
        "total_timeout_sec": float(raw["total_timeout_sec"]),
        "max_body_bytes": raw["max_body_bytes"],
        "circuit_breaker": {
            "threshold": threshold,
            "cooldown_sec": float(cooldown),
        },
        "parallelism": 1,
        "fetch_pipeline_used": True,
    }


def _project_validation_report(raw: dict[str, Any], request: LiveValidationRequest) -> LiveValidationResult:
    if raw.get("target") != request.target or raw.get("url_type") != request.url_type:
        raise LiveValidationServiceError(502, "live validation result does not match the request")
    if raw.get("max_urls") != request.max_urls or raw.get("max_urls_applied") != request.max_urls:
        raise LiveValidationServiceError(502, "live validation result does not match the request")
    if raw.get("verdict") not in {"pass", "warn"} or raw.get("verdict_reason") != "small-live-validation":
        raise LiveValidationServiceError(502, "live validation result is invalid")

    for key in _RAW_COUNT_FIELDS:
        if not _is_nonnegative_int(raw.get(key)) or raw[key] > 1_000_000:
            raise LiveValidationServiceError(502, "live validation result is invalid")
    attempted = raw["attempted_url_count"]
    if attempted > request.max_urls:
        raise LiveValidationServiceError(502, "live validation result exceeded the requested URL limit")
    if raw["excluded_unsafe_url_count"] != 0:
        raise LiveValidationServiceError(502, "live validation planner URL safety check failed")

    fetch_metrics = raw.get("fetch_metrics")
    if not isinstance(fetch_metrics, dict):
        raise LiveValidationServiceError(502, "live validation network metrics are invalid")
    network_requests = fetch_metrics.get("network_requests")
    retry_count = fetch_metrics.get("retry_count")
    backoff_count = fetch_metrics.get("backoff_count")
    if (
        not _is_nonnegative_int(network_requests)
        or network_requests > request.max_urls
        or retry_count != 0
        or backoff_count != 0
    ):
        raise LiveValidationServiceError(502, "live validation network budget was exceeded")

    elapsed = raw.get("elapsed_seconds")
    estimated = raw.get("estimated_full_refetch_runtime_seconds")
    if not _is_finite_nonnegative(elapsed) or float(elapsed) > TOTAL_TIMEOUT_SECONDS:
        raise LiveValidationServiceError(502, "live validation result timing is invalid")
    if not _is_finite_nonnegative(estimated):
        raise LiveValidationServiceError(502, "live validation result timing is invalid")

    samples_raw = raw.get("sample_results")
    if not isinstance(samples_raw, list) or len(samples_raw) != attempted:
        raise LiveValidationServiceError(502, "live validation result is invalid")
    samples = [_parse_validation_sample(item) for item in samples_raw]
    sample_urls = [item["url"] for item in samples]
    if len(set(sample_urls)) != len(sample_urls):
        raise LiveValidationServiceError(502, "live validation result is invalid")

    expected_url_type = {
        "race-result": "result_page",
        "race-detail": "race_detail",
        "horse-detail": "horse_detail",
        "pedigree": "pedigree",
    }.get(request.url_type)
    if expected_url_type and any(item["url_type"] != expected_url_type for item in samples):
        raise LiveValidationServiceError(502, "live validation result does not match the request")

    recomputed = {
        "attempted_url_count": len(samples),
        "http_success_count": sum(1 for item in samples if 200 <= item["http_status"] < 300),
        "parse_success_count": sum(1 for item in samples if item["parse_status"] == "parse_success"),
        "would_fix_count": sum(1 for item in samples if item["would_fix_columns"]),
        "no_downgrade_skip_count": sum(1 for item in samples if item["action"] == "no-downgrade-skip"),
    }
    recomputed["http_error_count"] = attempted - recomputed["http_success_count"]
    recomputed["parse_failed_count"] = attempted - recomputed["parse_success_count"]
    recomputed["would_not_fix_count"] = attempted - recomputed["would_fix_count"]
    recomputed["repairable_from_live_count"] = recomputed["would_fix_count"]
    for key, value in recomputed.items():
        if raw.get(key) != value:
            raise LiveValidationServiceError(502, "live validation result counts are inconsistent")

    recomputed_verdict = "pass" if attempted > 0 and recomputed["parse_success_count"] > 0 else "warn"
    if raw["verdict"] != recomputed_verdict:
        raise LiveValidationServiceError(502, "live validation verdict is inconsistent")

    actions = _safe_string_list(raw.get("recommended_next_actions"), max_items=12, max_length=240)
    if actions is None:
        raise LiveValidationServiceError(502, "live validation result is invalid")

    flags_raw = raw.get("safety_flags")
    if not isinstance(flags_raw, dict) or any(flags_raw.get(key) is not True for key in _VALIDATION_SAFETY_FLAGS):
        raise LiveValidationServiceError(502, "live validation safety checks failed")
    flags = {key: True for key in _VALIDATION_SAFETY_FLAGS}

    projected = {
        "target": request.target,
        "url_type": request.url_type,
        "max_urls_applied": request.max_urls,
        "attempted_url_count": attempted,
        "http_success_count": recomputed["http_success_count"],
        "http_error_count": recomputed["http_error_count"],
        "parse_success_count": recomputed["parse_success_count"],
        "parse_error_count": recomputed["parse_failed_count"],
        "would_fix_count": recomputed["would_fix_count"],
        "would_not_fix_count": recomputed["would_not_fix_count"],
        "no_downgrade_count": recomputed["no_downgrade_skip_count"],
        "repairable_count": recomputed["repairable_from_live_count"],
        "excluded_schema_review_count": raw["excluded_schema_review_count"],
        "excluded_domain_allowed_count": raw["excluded_domain_allowed_count"],
        "excluded_metadata_repair_count": raw["excluded_metadata_repair_count"],
        "excluded_cache_available_count": raw["excluded_cache_available_count"],
        "elapsed_seconds": float(elapsed),
        "estimated_full_refetch_runtime_seconds": float(estimated),
        "sample_results": samples,
        "recommended_next_actions": actions,
        "rate_limit_policy": _parse_rate_limit_policy(raw.get("rate_limit_policy"), request),
        "safety_flags": flags,
        "verdict": recomputed_verdict,
        "verdict_reason": "small-live-validation",
    }
    return LiveValidationResult.model_validate(projected)


def _snapshot_runtime_json(source: Path, destination: Path) -> None:
    """Copy one bounded, regular JSON object into the request-owned workspace."""

    try:
        if source.is_symlink():
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        metadata = source.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_PLANNER_INPUT_BYTES
        ):
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        with source.open("rb") as handle:
            encoded = handle.read(MAX_PLANNER_INPUT_BYTES + 1)
        if len(encoded) > MAX_PLANNER_INPUT_BYTES:
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        decoded = json.loads(encoded.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        destination.write_bytes(encoded)
    except LiveValidationServiceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveValidationServiceError(503, "live validation prerequisites are unavailable") from exc


def _require_runtime_database(path: Path, required_schema: dict[str, set[str]]) -> None:
    try:
        if path.is_symlink():
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            for table, required_columns in required_schema.items():
                rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
                present_columns = {str(row[1]) for row in rows if len(row) > 1}
                if not required_columns.issubset(present_columns):
                    raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        finally:
            connection.close()
    except LiveValidationServiceError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise LiveValidationServiceError(503, "live validation prerequisites are unavailable") from exc


def _create_empty_cache_database(path: Path) -> None:
    """Create a request-scoped empty cache without mutating persistent storage."""

    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE http_cache (
                    normalized_url TEXT PRIMARY KEY,
                    final_url TEXT NOT NULL,
                    status INTEGER NOT NULL,
                    headers_json TEXT NOT NULL,
                    body BLOB NOT NULL,
                    fetched_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        raise LiveValidationServiceError(500, "live validation workspace is unavailable") from exc


def _read_json_report(path: Path, *, max_bytes: int, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink():
            raise LiveValidationServiceError(502, f"{label} report is invalid")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise LiveValidationServiceError(502, f"{label} report is invalid")
        with path.open("rb") as handle:
            encoded = handle.read(max_bytes + 1)
        if len(encoded) > max_bytes:
            raise LiveValidationServiceError(502, f"{label} report is invalid")
        raw = json.loads(encoded.decode("utf-8"))
    except LiveValidationServiceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveValidationServiceError(502, f"{label} report is invalid") from exc
    if not isinstance(raw, dict):
        raise LiveValidationServiceError(502, f"{label} report is invalid")
    return raw


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.error("live validation subprocess did not exit after kill")


async def _read_stream_limited(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise _OutputLimitExceeded
        chunks.append(chunk)


async def run_bounded_command(command: tuple[str, ...], cwd: Path, timeout_seconds: float, stage: str) -> None:
    """Run one fixed argv command with shell disabled and capped pipe reads."""

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise LiveValidationServiceError(500, f"live validation {stage} is unavailable") from exc

    stdout_task = asyncio.create_task(_read_stream_limited(process.stdout, MAX_STDOUT_BYTES))
    stderr_task = asyncio.create_task(_read_stream_limited(process.stderr, MAX_STDERR_BYTES))
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, wait_task)
    try:
        _, _, return_code = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        await _terminate_process(process)
        raise LiveValidationServiceError(504, "live validation timed out") from exc
    except _OutputLimitExceeded as exc:
        await _terminate_process(process)
        raise LiveValidationServiceError(502, f"live validation {stage} output exceeded the limit") from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if return_code != 0:
        raise LiveValidationServiceError(500, f"live validation {stage} failed")


class LiveValidationService:
    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        planner_script: Path = PLANNER_SCRIPT,
        validator_script: Path = VALIDATOR_SCRIPT,
        data_dir: Path | None = None,
        runtime_input_dir: Path | None = None,
        command_runner: CommandRunner = run_bounded_command,
    ) -> None:
        self.project_root = project_root.resolve()
        self.planner_script = planner_script.resolve()
        self.validator_script = validator_script.resolve()
        self.command_runner = command_runner
        self.data_dir = (data_dir or (self.project_root / "keiba" / "data")).resolve()
        self._input_configuration_valid = True
        if runtime_input_dir is not None:
            self.runtime_input_dir = runtime_input_dir.resolve()
        else:
            configured_input_dir = (os.environ.get("LIVE_VALIDATION_INPUT_DIR") or "").strip()
            if configured_input_dir:
                configured_path = Path(configured_input_dir)
                if not configured_path.is_absolute():
                    self._input_configuration_valid = False
                    self.runtime_input_dir = self.data_dir / "live-validation-inputs"
                else:
                    self.runtime_input_dir = configured_path.resolve()
            else:
                self.runtime_input_dir = self.data_dir / "live-validation-inputs"

    def _snapshot_planner_inputs(self, workspace: Path) -> dict[str, Path]:
        if not self._input_configuration_valid:
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")

        _require_runtime_database(
            self.data_dir / "keiba_ultimate.db",
            {"race_results_ultimate": {"race_id", "data"}},
        )

        snapshot_dir = workspace / "inputs"
        try:
            snapshot_dir.mkdir(mode=0o700)
        except OSError as exc:
            raise LiveValidationServiceError(500, "live validation workspace is unavailable") from exc

        snapshots: dict[str, Path] = {}
        for filename in _PLANNER_INPUT_FILENAMES:
            destination = snapshot_dir / filename
            _snapshot_runtime_json(self.runtime_input_dir / filename, destination)
            snapshots[filename] = destination

        persistent_cache = self.data_dir / "fetch_cache.db"
        if persistent_cache.is_symlink():
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        if persistent_cache.exists():
            _require_runtime_database(
                persistent_cache,
                {"http_cache": {"normalized_url", "final_url", "expires_at"}},
            )
            snapshots["fetch_cache.db"] = persistent_cache
        else:
            empty_cache = workspace / "empty-fetch-cache.db"
            _create_empty_cache_database(empty_cache)
            snapshots["fetch_cache.db"] = empty_cache

        persistent_pedigree_cache = self.data_dir / "pedigree_cache.db"
        if persistent_pedigree_cache.is_symlink():
            raise LiveValidationServiceError(503, "live validation prerequisites are unavailable")
        if persistent_pedigree_cache.exists():
            _require_runtime_database(
                persistent_pedigree_cache,
                {"pedigree_cache": {"horse_id"}},
            )
            snapshots["pedigree_cache.db"] = persistent_pedigree_cache
        else:
            # The planner checks existence before opening this optional cache.
            snapshots["pedigree_cache.db"] = workspace / "missing-pedigree-cache.db"
        return snapshots

    def _planner_command(
        self,
        request: LiveValidationRequest,
        output_path: Path,
        planner_inputs: dict[str, Path],
    ) -> tuple[str, ...]:
        return (
            sys.executable,
            str(self.planner_script),
            "--input-audit",
            str(planner_inputs["scrape_missingness_audit.json"]),
            "--input-p0-plan",
            str(planner_inputs["p0_scrape_repair_plan.json"]),
            "--input-cache-diagnosis",
            str(planner_inputs["p0_cache_coverage_diagnosis.json"]),
            "--target",
            request.target,
            "--max-targets",
            str(request.max_urls),
            "--output",
            str(output_path),
            "--db-path",
            str(self.data_dir / "keiba_ultimate.db"),
            "--cache-db",
            str(planner_inputs["fetch_cache.db"]),
            "--pedigree-cache-db",
            str(planner_inputs["pedigree_cache.db"]),
        )

    def _validator_command(
        self,
        request: LiveValidationRequest,
        planner_output: Path,
        validator_output: Path,
        cache_db: Path,
    ) -> tuple[str, ...]:
        return (
            sys.executable,
            str(self.validator_script),
            "--input-refetch-plan",
            str(planner_output),
            "--target",
            request.target,
            "--url-type",
            request.url_type,
            "--max-urls",
            str(request.max_urls),
            "--output",
            str(validator_output),
            "--cache-db",
            str(cache_db),
        )

    async def run(self, request: LiveValidationRequest) -> LiveValidationResponse:
        if not self.planner_script.is_file() or not self.validator_script.is_file():
            raise LiveValidationServiceError(500, "live validation service is unavailable")

        started_at = time.monotonic()
        try:
            workspace = Path(tempfile.mkdtemp(prefix="keiba-live-validation-"))
        except OSError as exc:
            raise LiveValidationServiceError(500, "live validation workspace is unavailable") from exc

        planner_output = workspace / "planner.json"
        validator_output = workspace / "validation.json"
        try:
            planner_inputs = await asyncio.to_thread(self._snapshot_planner_inputs, workspace)
            remaining = TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started_at)
            if remaining <= 0:
                raise LiveValidationServiceError(504, "live validation timed out")
            await self.command_runner(
                self._planner_command(request, planner_output, planner_inputs),
                self.project_root,
                min(PLANNER_TIMEOUT_SECONDS, remaining),
                "planner",
            )
            planner_report = _read_json_report(
                planner_output,
                max_bytes=MAX_PLANNER_REPORT_BYTES,
                label="planner",
            )
            _validate_planner_report(planner_report, request)

            remaining = TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started_at)
            if remaining <= 0:
                raise LiveValidationServiceError(504, "live validation timed out")
            await self.command_runner(
                self._validator_command(
                    request,
                    planner_output,
                    validator_output,
                    planner_inputs["fetch_cache.db"],
                ),
                self.project_root,
                remaining,
                "validator",
            )
            validation_report = _read_json_report(
                validator_output,
                max_bytes=MAX_VALIDATION_REPORT_BYTES,
                label="validation",
            )
            result = _project_validation_report(validation_report, request)
            return LiveValidationResponse(
                live_validation=True,
                bounded=True,
                external_http=True,
                read_only=True,
                execution_enabled=False,
                result=result,
            )
        finally:
            try:
                shutil.rmtree(workspace)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.exception("failed to clean live validation workspace")
                raise LiveValidationServiceError(500, "live validation cleanup failed") from exc


class LiveValidationCoordinator:
    """Process-local single-flight and per-user cooldown guard."""

    def __init__(self, *, cooldown_seconds: float = USER_COOLDOWN_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._in_flight = False
        self._last_finished: dict[str, float] = {}

    def acquire(self, user_id: str) -> None:
        now = self.clock()
        with self._lock:
            if self._in_flight:
                raise LiveValidationServiceError(409, "live validation is already in progress")
            last_finished = self._last_finished.get(user_id)
            if last_finished is not None:
                remaining = self.cooldown_seconds - (now - last_finished)
                if remaining > 0:
                    retry_after = max(1, math.ceil(remaining))
                    raise LiveValidationServiceError(
                        429,
                        "live validation cooldown is active",
                        headers={"Retry-After": str(retry_after)},
                    )
            if len(self._last_finished) > 1024:
                cutoff = now - max(self.cooldown_seconds * 2, 1.0)
                self._last_finished = {key: value for key, value in self._last_finished.items() if value >= cutoff}
            self._in_flight = True

    def release(self, user_id: str) -> None:
        with self._lock:
            self._last_finished[user_id] = self.clock()
            self._in_flight = False


live_validation_service = LiveValidationService()
live_validation_coordinator = LiveValidationCoordinator()
