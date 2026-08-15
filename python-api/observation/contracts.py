from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_ENVIRONMENTS = frozenset({"staging", "production"})


class ObservationContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ObservationContractError("canonical-json-invalid") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Any) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ObservationContractError("model-artifact-unavailable") from exc
    return digest.hexdigest()


def parse_timestamp(value: Any, *, code: str) -> datetime:
    if not isinstance(value, str):
        raise ObservationContractError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationContractError(code) from exc
    if parsed.tzinfo is None:
        raise ObservationContractError(code)
    return parsed.astimezone(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ObservationContractError("timestamp-timezone-required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite_number(value: Any, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ObservationContractError("numeric-value-invalid")
    parsed = float(value)
    if minimum is not None and parsed < minimum:
        raise ObservationContractError("numeric-value-invalid")
    return parsed


def normalize_feature_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return normalize_feature_value(value.item())
    if isinstance(value, datetime):
        return timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def feature_row_sha256(columns: Iterable[str], values: Iterable[Any]) -> str:
    pairs = [
        [str(column), normalize_feature_value(value)]
        for column, value in zip(columns, values)
    ]
    return canonical_sha256(pairs)


def with_payload_sha256(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if "payload_sha256" in result:
        raise ObservationContractError("payload-digest-prepopulated")
    result["payload_sha256"] = canonical_sha256(result)
    return result


def stable_uuid(namespace: str, payload_sha256: str) -> str:
    if DIGEST_RE.fullmatch(payload_sha256) is None:
        raise ObservationContractError("payload-digest-invalid")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"keiba-ai:{namespace}:{payload_sha256}"))


def require_exact_keys(value: Any, keys: frozenset[str], *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ObservationContractError(code)
    return value


def require_identifier(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise ObservationContractError(code)
    return value


def require_digest(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise ObservationContractError(code)
    return value


def require_commit(value: Any) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise ObservationContractError("candidate-commit-invalid")
    return value
