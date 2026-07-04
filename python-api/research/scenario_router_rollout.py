from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore
from research.scenario_router_canary import evaluate_scenario_router_canary


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _normalize_steps(steps: list[int] | None) -> list[int]:
    raw = steps or [5, 20, 50, 100]
    out = sorted({max(1, min(100, _safe_int(x, 0))) for x in raw if _safe_int(x, 0) > 0})
    if not out:
        out = [5, 20, 50, 100]
    if 100 not in out:
        out.append(100)
    return sorted(set(out))


def _router_mode_for_percent(percent: int) -> str:
    p = max(0, min(100, int(percent)))
    if p <= 0:
        return "shadow"
    if p >= 100:
        return "active"
    return "canary"


def _status_for_percent(percent: int) -> str:
    p = max(0, min(100, int(percent)))
    if p <= 0:
        return "SHADOW_ONLY"
    if p >= 100:
        return "FULL_ACTIVE"
    return f"CANARY_{p}"


def _default_rollout(target: str) -> dict[str, Any]:
    return {
        "rollout_id": "",
        "target": str(target or "win"),
        "current_percent": 0,
        "previous_percent": 0,
        "router_mode": "shadow",
        "status": "SHADOW_ONLY",
        "last_decision": "INIT",
        "last_reason": "initial rollout state",
        "started_at": "",
        "updated_at": "",
    }


def _next_step(current_percent: int, steps: list[int]) -> int:
    cur = max(0, min(100, int(current_percent)))
    for s in steps:
        if int(s) > cur:
            return int(s)
    return cur


def _build_plan(
    *,
    current: dict[str, Any],
    canary_eval: dict[str, Any],
    rollout_steps: list[int],
) -> dict[str, Any]:
    current_percent = max(0, min(100, _safe_int(current.get("current_percent"), 0)))
    decision = str(canary_eval.get("decision") or "HOLD").upper()
    reason = str(canary_eval.get("reason") or "")

    action = "HOLD"
    next_percent = current_percent
    next_status = str(current.get("status") or _status_for_percent(current_percent))

    if decision in {"NEEDS_MORE_DATA", "HOLD"}:
        action = "HOLD"
        next_percent = current_percent
        next_status = "HOLD"
    elif decision == "INCREASE_CANARY":
        if current_percent >= 100:
            action = "FULL_ACTIVE_NOOP"
            next_percent = 100
            next_status = "FULL_ACTIVE"
        else:
            step = _next_step(current_percent, rollout_steps)
            if step <= current_percent:
                action = "HOLD_MAX_REACHED"
                next_percent = current_percent
                next_status = _status_for_percent(current_percent)
            else:
                action = "INCREASE"
                next_percent = int(step)
                next_status = _status_for_percent(step)
    elif decision == "ROLLBACK_TO_SHADOW":
        action = "ROLLBACK"
        next_percent = 0
        next_status = "ROLLBACK"
    elif decision == "STOP_CANARY":
        action = "STOP"
        next_percent = 0
        next_status = "STOPPED"
    else:
        action = "HOLD_UNKNOWN_DECISION"
        next_percent = current_percent
        next_status = "HOLD"
        if not reason:
            reason = f"unknown decision: {decision}"

    return {
        "decision": decision,
        "action": action,
        "reason": reason,
        "from_percent": int(current_percent),
        "to_percent": int(next_percent),
        "next_router_mode": _router_mode_for_percent(next_percent),
        "next_status": next_status,
    }


def get_scenario_router_rollout_status(
    *,
    mlops_db_path: str,
    target: str | None = None,
    create_if_missing: bool = True,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")
    row = db_store.get_router_rollout(target=tgt, create_if_missing=bool(create_if_missing))
    if not row:
        row = _default_rollout(tgt)
    return row


def evaluate_scenario_router_rollout(
    *,
    mlops_db_path: str,
    race_db_path: str,
    target: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_races: int = 30,
    canary_percent: int | None = None,
    max_fallback_rate: float = 0.50,
    max_no_model_rate: float = 0.05,
    min_roi_lift: float = -0.03,
    min_hit_rate_lift: float = -0.02,
    stake_per_race: int = 100,
    rollout_steps: list[int] | None = None,
    create_rollout_if_missing: bool = False,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")

    current = get_scenario_router_rollout_status(
        mlops_db_path=mlops_db_path,
        target=tgt,
        create_if_missing=bool(create_rollout_if_missing),
        store=db_store,
    )

    steps = _normalize_steps(rollout_steps)

    if canary_percent is not None:
        effective_canary_percent = int(canary_percent)
    else:
        cur_percent = int(current.get("current_percent") or 0)
        effective_canary_percent = int(steps[0]) if cur_percent <= 0 else int(cur_percent)

    canary_eval = evaluate_scenario_router_canary(
        mlops_db_path=mlops_db_path,
        race_db_path=race_db_path,
        date_from=date_from,
        date_to=date_to,
        target=tgt,
        min_races=int(min_races),
        canary_percent=int(effective_canary_percent),
        max_fallback_rate=float(max_fallback_rate),
        max_no_model_rate=float(max_no_model_rate),
        min_roi_lift=float(min_roi_lift),
        min_hit_rate_lift=float(min_hit_rate_lift),
        stake_per_race=int(stake_per_race),
        store=db_store,
    )

    plan = _build_plan(current=current, canary_eval=canary_eval, rollout_steps=steps)

    return {
        "target": tgt,
        "current": current,
        "evaluation": canary_eval,
        "plan": plan,
        "rollout_steps": steps,
        "effective_canary_percent": int(effective_canary_percent),
    }


def apply_scenario_router_rollout(
    *,
    mlops_db_path: str,
    race_db_path: str,
    target: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_races: int = 30,
    canary_percent: int | None = None,
    max_fallback_rate: float = 0.50,
    max_no_model_rate: float = 0.05,
    min_roi_lift: float = -0.03,
    min_hit_rate_lift: float = -0.02,
    stake_per_race: int = 100,
    rollout_steps: list[int] | None = None,
    apply_updates: bool = False,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")

    data = evaluate_scenario_router_rollout(
        mlops_db_path=mlops_db_path,
        race_db_path=race_db_path,
        target=tgt,
        date_from=date_from,
        date_to=date_to,
        min_races=int(min_races),
        canary_percent=canary_percent,
        max_fallback_rate=float(max_fallback_rate),
        max_no_model_rate=float(max_no_model_rate),
        min_roi_lift=float(min_roi_lift),
        min_hit_rate_lift=float(min_hit_rate_lift),
        stake_per_race=int(stake_per_race),
        rollout_steps=rollout_steps,
        create_rollout_if_missing=bool(apply_updates),
        store=db_store,
    )

    current = dict(data.get("current") or {})
    plan = dict(data.get("plan") or {})
    canary_summary = ((data.get("evaluation") or {}).get("summary") or {})

    if not bool(apply_updates):
        return {
            **data,
            "applied": False,
            "event_saved": False,
            "warnings": ["apply_updates=false: no rollout state was updated"],
        }

    if not current or not current.get("rollout_id"):
        current = get_scenario_router_rollout_status(
            mlops_db_path=mlops_db_path,
            target=tgt,
            create_if_missing=True,
            store=db_store,
        )

    updated = db_store.update_router_rollout(
        rollout_id=str(current.get("rollout_id") or ""),
        target=tgt,
        current_percent=int(plan.get("to_percent") or 0),
        previous_percent=int(plan.get("from_percent") or 0),
        router_mode=str(plan.get("next_router_mode") or "shadow"),
        status=str(plan.get("next_status") or "SHADOW_ONLY"),
        last_decision=str(plan.get("decision") or ""),
        last_reason=str(plan.get("reason") or ""),
    )

    event_id = db_store.insert_router_rollout_event(
        rollout_id=str((updated or {}).get("rollout_id") or current.get("rollout_id") or ""),
        target=tgt,
        from_percent=int(plan.get("from_percent") or 0),
        to_percent=int(plan.get("to_percent") or 0),
        decision=str(plan.get("decision") or ""),
        action=str(plan.get("action") or ""),
        reason=str(plan.get("reason") or ""),
        summary=dict(canary_summary or {}),
    )

    return {
        **data,
        "current": updated or current,
        "applied": True,
        "event_saved": True,
        "event_id": str(event_id),
        "warnings": [],
    }
