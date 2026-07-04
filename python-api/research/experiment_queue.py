from __future__ import annotations

from typing import Any

import yaml

from .experiment_registry import ExperimentOpsStore


def submit_experiment_spec(
    *,
    spec: dict[str, Any],
    name: str = "",
    priority: int = 100,
    metadata: dict[str, Any] | None = None,
    store: ExperimentOpsStore | None = None,
) -> dict[str, Any]:
    ops = store or ExperimentOpsStore()
    reg = ops.register_spec(name=name, spec=spec, metadata=metadata)
    job_id = ops.enqueue(spec_id=str(reg["spec_id"]), priority=int(priority))
    return {
        "job_id": int(job_id),
        "spec_id": str(reg["spec_id"]),
        "spec_hash": str(reg["spec_hash"]),
    }


def submit_experiment_yaml(
    *,
    yaml_text: str,
    name: str = "",
    priority: int = 100,
    metadata: dict[str, Any] | None = None,
    store: ExperimentOpsStore | None = None,
) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(yaml_text or "")
    except Exception as e:
        raise ValueError(f"invalid yaml: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("yaml root must be a mapping object")
    return submit_experiment_spec(
        spec=parsed,
        name=name,
        priority=priority,
        metadata=metadata,
        store=store,
    )
