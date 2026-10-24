"""Opt-in real local context-candidate creation; never activate or purchase.

One normal authenticated train POST, then owner-scoped status GETs. Credentials
stay in memory. An accepted job is persisted immediately and never resent.
This is a local training integration check, not a profitability release gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import Request

import smoke_local_authenticated_readonly as auth
from evaluate_db_shared_venues import preservation_receipt

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports/generated/context-features"
PIPELINE = "local-db-context-v1"


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def execute(device):
    directory = OUTPUT / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-normal-job")
    directory.mkdir(parents=True, exist_ok=False)
    before = preservation_receipt()
    save(directory / "before.json", before)
    opener = auth.secure_opener()
    status, token = auth.authenticate(opener, auth.configuration(ROOT))
    if status != 200:
        save(directory / "failure.json", {"stage": "authentication", "status": status})
        raise ValueError("context-local-authentication-unavailable")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    base = "http://127.0.0.1:3000"
    status, capability = auth.request_json(opener, Request(base + "/api/ml/train/capability", headers=headers))
    capability = capability if isinstance(capability, dict) else {}
    profiles = capability.get("feature_profiles", [])
    profiles = profiles if isinstance(profiles, list) else []
    if status != 200 or capability.get("enabled") is not True or not any(
        item.get("pipeline") == PIPELINE and item.get("feature_count") == 80 and item.get("reference_only") is True
        for item in profiles if isinstance(item, dict)
    ):
        save(directory / "failure.json", {"stage": "capability", "status": status, "context80_available": False})
        raise ValueError("context-local-capability-unavailable")
    payload = {"target": "speed_deviation", "model_type": "lightgbm", "test_size": .2,
        "cv_folds": 5, "feature_pipeline": PIPELINE, "compute_device": device, "reuse_features": True,
        "use_sqlite": True, "ultimate_mode": True, "use_optimizer": True, "use_optuna": False,
        "training_date_from": "2016-01", "training_date_to": "2026-09"}
    save(directory / "request.json", payload)
    status, accepted = auth.request_json(opener, Request(base + "/api/ml/train/start", headers=headers,
        data=json.dumps(payload).encode(), method="POST"))
    job_id = accepted.get("job_id") if isinstance(accepted, dict) else None
    import re
    if status != 200 or not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        save(directory / "failure.json", {"stage": "acceptance", "status": status,
            "automatic_retry": False, "acceptance_may_be_unknown": status not in (400, 401, 403, 409, 422)})
        raise ValueError("context-local-acceptance-not-verified-no-retry")
    save(directory / "accepted.json", {"job_id": job_id, "status": accepted.get("status"), "automatic_resubmit": False})
    print(json.dumps({"stage": "accepted", "directory": str(directory), "job_id": job_id}), flush=True)
    deadline = time.monotonic() + 900
    last = None
    while time.monotonic() < deadline:
        status, body = auth.request_json(opener, Request(base + "/api/ml/train/status/" + job_id, headers=headers))
        if status == 200 and isinstance(body, dict):
            state = body.get("status")
            progress = (state, body.get("pct"), body.get("progress"))
            if progress != last:
                print(json.dumps({"stage": "progress", "status": state, "pct": body.get("pct"),
                    "progress": body.get("progress")}, ensure_ascii=False), flush=True)
                last = progress
            if state in {"completed", "failed", "error", "cancelled"}:
                result = body.get("result") if isinstance(body.get("result"), dict) else {}
                save(directory / "terminal.json", {"job_id": job_id, "status": state,
                    "result": result, "failure_code": body.get("failure_code"),
                    "activated": False, "purchased": False})
                after = preservation_receipt()
                save(directory / "after.json", after)
                save(directory / "protection.json", {"protected_state_unchanged": before == after,
                    "candidate_created": state == "completed", "active_model_changed_by_this_check": False})
                print(json.dumps({"stage": "terminal", "status": state, "directory": str(directory),
                    "model_id": result.get("model_id"), "feature_count": result.get("feature_count"),
                    "protected_state_unchanged": before == after}), flush=True)
                return directory
        time.sleep(10)
    save(directory / "pending.json", {"job_id": job_id, "reason": "bounded-observation-deadline",
        "job_not_cancelled": True, "automatic_resubmit": False})
    print(json.dumps({"stage": "pending", "directory": str(directory), "job_id": job_id}), flush=True)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    args = parser.parse_args()
    if args.run:
        execute(args.device)
    else:
        print(json.dumps({"pipeline": PIPELINE, "run_started": False, "activation": False}))
