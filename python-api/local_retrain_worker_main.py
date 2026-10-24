"""Isolated entrypoint for one durable local model-training job.

The API sends a small, strictly validated JSON envelope over stdin so the
preparation capability never appears in the process command line.  The durable
ledger remains the source of truth for progress, leases, and results.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass
from typing import Any


MAX_ENVELOPE_BYTES = 256 * 1024
LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test", "ci"})
PREPARATION_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
ENVELOPE_KEYS = frozenset(
    {"schema_version", "mode", "job_id", "preparation_token", "request"}
)
ENVELOPE_V2_KEYS = ENVELOPE_KEYS | {"execution_receipt"}
BOUND_SHARED_PIPELINES = frozenset({
    "local-db-shared-v1", "local-db-shared-optimized-v1", "local-db-context-v1",
})


class WorkerEnvelopeError(ValueError):
    """A sanitized worker-input validation failure."""


@dataclass(frozen=True)
class WorkerEnvelope:
    mode: str
    job_id: str
    preparation_token: str | None
    request: dict[str, Any] | None
    execution_receipt: dict[str, Any] | None = None


def _canonical_uuid4(value: object) -> str:
    if not isinstance(value, str):
        raise WorkerEnvelopeError("worker-job-id-invalid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WorkerEnvelopeError("worker-job-id-invalid") from exc
    canonical = str(parsed)
    if parsed.version != 4 or canonical != value.lower():
        raise WorkerEnvelopeError("worker-job-id-invalid")
    return canonical


def parse_envelope(raw: bytes) -> WorkerEnvelope:
    if not raw or len(raw) > MAX_ENVELOPE_BYTES:
        raise WorkerEnvelopeError("worker-envelope-size-invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerEnvelopeError("worker-envelope-json-invalid") from exc
    if not isinstance(value, dict):
        raise WorkerEnvelopeError("worker-envelope-schema-invalid")
    version = value.get("schema_version")
    if version not in {1, 2} or type(version) is not int:
        raise WorkerEnvelopeError("worker-envelope-version-invalid")
    if frozenset(value) != (ENVELOPE_KEYS if version == 1 else ENVELOPE_V2_KEYS):
        raise WorkerEnvelopeError("worker-envelope-schema-invalid")
    execution_receipt = value.get("execution_receipt")
    if version == 2 and execution_receipt is not None:
        from training.db_shared_acceleration import _validate_receipt_structure
        try:
            _validate_receipt_structure(execution_receipt)
        except (ValueError, TypeError) as exc:
            raise WorkerEnvelopeError("worker-execution-receipt-invalid") from exc

    mode = value.get("mode")
    if mode not in {"prepare", "queued"}:
        raise WorkerEnvelopeError("worker-mode-invalid")
    job_id = _canonical_uuid4(value.get("job_id"))
    preparation_token = value.get("preparation_token")
    request = value.get("request")
    if mode == "prepare":
        if (
            not isinstance(preparation_token, str)
            or PREPARATION_TOKEN_RE.fullmatch(preparation_token) is None
        ):
            raise WorkerEnvelopeError("worker-preparation-token-invalid")
        if not isinstance(request, dict):
            raise WorkerEnvelopeError("worker-request-invalid")
    elif preparation_token is not None or request is not None:
        raise WorkerEnvelopeError("worker-queued-envelope-invalid")

    return WorkerEnvelope(
        mode=mode,
        job_id=job_id,
        preparation_token=preparation_token,
        request=request,
        execution_receipt=execution_receipt,
    )


async def run_worker(envelope: WorkerEnvelope) -> None:
    environment = os.getenv("APP_ENV", "").strip().lower()
    if environment not in LOCAL_ENVIRONMENTS:
        raise WorkerEnvelopeError("worker-environment-forbidden")

    from pathlib import Path
    from training.db_shared_acceleration import (
        GPU_PYTHON, configure_worker_imports, validate_execution_receipt,
    )
    if envelope.execution_receipt is not None:
        # Fence runtime identity before importing the coordinator, creating a
        # snapshot, renewing preparation or acquiring the durable job lease.
        configure_worker_imports(envelope.execution_receipt)
        validate_execution_receipt(envelope.execution_receipt)
        if (envelope.mode == "prepare" and envelope.request is not None
                and (envelope.request.get("feature_pipeline") not in BOUND_SHARED_PIPELINES
                     or envelope.request.get("compute_device", "auto")
                     != envelope.execution_receipt["requested_device"])):
            raise WorkerEnvelopeError("worker-execution-request-mismatch")
    elif Path(sys.executable).resolve() == GPU_PYTHON.resolve():
        # Historical schema-v1 envelopes can never opt into GPU execution.
        raise WorkerEnvelopeError("worker-gpu-execution-receipt-required")

    from models import TrainRequest
    from routers import train

    if envelope.mode == "queued":
        await train._execute_queued_local_retrain(envelope.job_id)
        return
    assert envelope.preparation_token is not None
    assert envelope.request is not None
    request = TrainRequest(**envelope.request)
    await train._prepare_and_execute_local_retrain(
        envelope.job_id,
        request,
        envelope.preparation_token,
    )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_ENVELOPE_BYTES + 1)
        envelope = parse_envelope(raw)
        asyncio.run(run_worker(envelope))
    except Exception as exc:
        # stdout/stderr are redirected to a local file by the parent.  Do not
        # echo the envelope or its preparation capability.
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc(file=sys.stderr)
        return 1
    print(json.dumps({"ok": True}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
