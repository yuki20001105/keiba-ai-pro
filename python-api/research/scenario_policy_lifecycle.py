from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mlops import MLOpsStore


def _parse_dt(v: Any) -> datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _streak(actions: list[str], expected: str) -> int:
    n = 0
    for a in actions:
        if str(a or "").upper() == expected:
            n += 1
        else:
            break
    return n


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def apply_scenario_policy_lifecycle(
    *,
    mlops_db_path: str,
    target: str | None = None,
    lookback_evaluations: int = 5,
    raise_confirmations: int = 2,
    disable_confirmations: int = 2,
    watch_to_lower_threshold: int = 3,
    needs_more_data_to_watch_threshold: int = 3,
    cooldown_days: int = 7,
    priority_step: int = 10,
    apply_updates: bool = False,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))

    policies = db_store.list_scenario_model_policies(status="active", target=target)
    if not policies:
        return {
            "summary": {
                "evaluated_policies": 0,
                "confirmed_raise": 0,
                "confirmed_disable": 0,
                "confirmed_lower": 0,
                "cooldown_skipped": 0,
                "insufficient_history": 0,
                "updated_policies": 0,
                "saved_lifecycle_states": 0,
                "apply_updates": bool(apply_updates),
            },
            "actions": [],
            "warnings": ["no active policies for lifecycle evaluation"],
        }

    policy_ids = [str(p.get("policy_id") or "") for p in policies if str(p.get("policy_id") or "")]
    recent_map = db_store.list_recent_policy_evaluations(
        target=target,
        lookback_evaluations=int(lookback_evaluations),
        policy_ids=policy_ids,
    )
    lifecycle_map = db_store.get_policy_lifecycle_states(policy_ids=policy_ids)

    min_required = min(
        int(raise_confirmations),
        int(disable_confirmations),
        int(watch_to_lower_threshold),
        int(needs_more_data_to_watch_threshold),
    )
    min_required = max(1, int(min_required))

    now = datetime.utcnow()
    actions_out: list[dict[str, Any]] = []
    next_states: list[dict[str, Any]] = []

    for p in policies:
        policy_id = str(p.get("policy_id") or "")
        hist = recent_map.get(policy_id) or []
        raw_actions = [str(x.get("action") or "").upper() for x in hist]

        c_keep = _streak(raw_actions, "KEEP")
        c_raise = _streak(raw_actions, "RAISE_PRIORITY")
        c_lower = _streak(raw_actions, "LOWER_PRIORITY")
        c_disable = _streak(raw_actions, "DISABLE")
        c_watch = _streak(raw_actions, "WATCH")
        c_needs = _streak(raw_actions, "NEEDS_MORE_DATA")

        prev = lifecycle_map.get(policy_id) or {}
        cooldown_until = _parse_dt(prev.get("cooldown_until"))
        in_cooldown = bool(cooldown_until and cooldown_until > now)

        lifecycle_action = "NO_CHANGE"
        reason = "no lifecycle trigger"
        lifecycle_status = "steady"

        if in_cooldown:
            lifecycle_action = "COOLDOWN_SKIP"
            reason = f"cooldown active until {cooldown_until.strftime('%Y-%m-%d %H:%M:%S')}"
            lifecycle_status = "cooldown"
        elif len(raw_actions) < min_required:
            lifecycle_action = "INSUFFICIENT_HISTORY"
            reason = f"history={len(raw_actions)} < required={min_required}"
            lifecycle_status = "pending"
        elif c_disable >= int(disable_confirmations):
            lifecycle_action = "CONFIRM_DISABLE"
            reason = f"DISABLE observed {c_disable} times consecutively"
            lifecycle_status = "confirmed_disable"
        elif c_raise >= int(raise_confirmations):
            lifecycle_action = "CONFIRM_RAISE_PRIORITY"
            reason = f"RAISE_PRIORITY observed {c_raise} times consecutively"
            lifecycle_status = "confirmed_raise"
        elif c_watch >= int(watch_to_lower_threshold):
            lifecycle_action = "CONFIRM_LOWER_PRIORITY"
            reason = f"WATCH observed {c_watch} times consecutively"
            lifecycle_status = "confirmed_lower"
        elif c_needs >= int(needs_more_data_to_watch_threshold):
            lifecycle_action = "KEEP_PENDING"
            reason = f"NEEDS_MORE_DATA observed {c_needs} times consecutively"
            lifecycle_status = "pending_data"

        next_cooldown = cooldown_until
        if lifecycle_action in {"CONFIRM_DISABLE", "CONFIRM_RAISE_PRIORITY", "CONFIRM_LOWER_PRIORITY"} and bool(apply_updates):
            next_cooldown = now + timedelta(days=max(0, int(cooldown_days)))

        next_states.append(
            {
                "policy_id": policy_id,
                "last_action": lifecycle_action,
                "consecutive_keep": int(c_keep),
                "consecutive_raise": int(c_raise),
                "consecutive_lower": int(c_lower),
                "consecutive_disable": int(c_disable),
                "consecutive_watch": int(c_watch),
                "consecutive_needs_more_data": int(c_needs),
                "cooldown_until": (
                    next_cooldown.strftime("%Y-%m-%d %H:%M:%S") if next_cooldown else ""
                ),
                "lifecycle_status": lifecycle_status,
            }
        )

        actions_out.append(
            {
                "policy_id": policy_id,
                "scenario_key": str(p.get("scenario_key") or ""),
                "scenario_value": str(p.get("scenario_value") or ""),
                "model_id": str(p.get("model_id") or ""),
                "raw_recent_actions": raw_actions,
                "lifecycle_action": lifecycle_action,
                "reason": reason,
            }
        )

    saved_states = db_store.upsert_policy_lifecycle_states(states=next_states)

    updated = 0
    if bool(apply_updates):
        confirmed = [
            a
            for a in actions_out
            if str(a.get("lifecycle_action") or "")
            in {"CONFIRM_RAISE_PRIORITY", "CONFIRM_LOWER_PRIORITY", "CONFIRM_DISABLE"}
        ]
        updated = db_store.apply_lifecycle_policy_actions(
            actions=confirmed,
            priority_step=max(1, int(priority_step)),
        )

    summary = {
        "evaluated_policies": int(len(actions_out)),
        "confirmed_raise": sum(1 for a in actions_out if str(a.get("lifecycle_action") or "") == "CONFIRM_RAISE_PRIORITY"),
        "confirmed_disable": sum(1 for a in actions_out if str(a.get("lifecycle_action") or "") == "CONFIRM_DISABLE"),
        "confirmed_lower": sum(1 for a in actions_out if str(a.get("lifecycle_action") or "") == "CONFIRM_LOWER_PRIORITY"),
        "cooldown_skipped": sum(1 for a in actions_out if str(a.get("lifecycle_action") or "") == "COOLDOWN_SKIP"),
        "insufficient_history": sum(1 for a in actions_out if str(a.get("lifecycle_action") or "") == "INSUFFICIENT_HISTORY"),
        "updated_policies": int(updated),
        "saved_lifecycle_states": int(saved_states),
        "apply_updates": bool(apply_updates),
    }

    return {
        "summary": summary,
        "actions": actions_out,
        "warnings": [],
    }
