from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore
from research.scenario_router_alerts import resolve_scenario_router_alert
from research.scenario_router_backtest import run_scenario_router_backtest
from research.scenario_router_canary import evaluate_scenario_router_canary
from research.scenario_router_rollout import get_scenario_router_rollout_status


DANGEROUS_ACTIONS = {"STOP_CANARY", "ROLLBACK_TO_SHADOW", "DISABLE_POLICY"}


def _resolve_context(
    *,
    store: MLOpsStore,
    alert_id: str | None,
    runbook_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rb: dict[str, Any] = {}
    al: dict[str, Any] = {}

    rid = str(runbook_id or "").strip()
    aid = str(alert_id or "").strip()

    if rid:
        rb = store.get_router_runbook_by_id(runbook_id=rid) or {}
        if not rb:
            raise ValueError(f"runbook_id not found: {rid}")
        if not aid:
            aid = str(rb.get("alert_id") or "")

    if aid:
        al = store.get_router_alert_by_id(alert_id=aid) or {}
        if not al:
            raise ValueError(f"alert_id not found: {aid}")

    if not al and not rb:
        raise ValueError("either alert_id or runbook_id is required")

    if not rb and aid:
        rows = store.list_router_runbooks(alert_id=aid, limit=1)
        rb = rows[0] if rows else {}

    return al, rb


def _action_candidate(action_type: str, reason: str, dangerous: bool = False, requires_policy_id: bool = False) -> dict[str, Any]:
    return {
        "action_type": str(action_type),
        "reason": str(reason),
        "dangerous": bool(dangerous),
        "requires_confirm": bool(dangerous),
        "requires_policy_id": bool(requires_policy_id),
    }


def _build_recommended_actions(*, alert: dict[str, Any], runbook: dict[str, Any]) -> list[dict[str, Any]]:
    decision = str(alert.get("decision") or "").upper()
    action = str(alert.get("action") or "").upper()
    alert_type = str(alert.get("alert_type") or "").upper()
    severity = str(alert.get("severity") or "WARNING").upper()

    out: list[dict[str, Any]] = []

    if decision == "STOP_CANARY" or action == "STOP" or alert_type in {"STOP_CANARY", "ACTION_STOP", "HIGH_NO_MODEL_RATE"}:
        out.append(_action_candidate("STOP_CANARY", "Stop canary and force safe shadow state", dangerous=True))
        out.append(_action_candidate("ROLLBACK_TO_SHADOW", "Rollback to shadow-only rollout state", dangerous=True))
    if decision == "ROLLBACK_TO_SHADOW" or action == "ROLLBACK" or alert_type in {"ROLLBACK_TO_SHADOW", "ACTION_ROLLBACK"}:
        out.append(_action_candidate("ROLLBACK_TO_SHADOW", "Rollback to shadow-only rollout state", dangerous=True))
    if alert_type in {"RUN_FAILED", "LOW_ROI_LIFT", "LOW_HIT_RATE_LIFT", "HIGH_FALLBACK_RATE"}:
        out.append(_action_candidate("RUN_CANARY_EVALUATE", "Re-evaluate canary guardrails with current data"))
        out.append(_action_candidate("RUN_ROUTER_BACKTEST", "Recompute router baseline and lift metrics"))

    if severity in {"CRITICAL", "WARNING"}:
        out.append(_action_candidate("RESOLVE_ALERT", "Resolve alert after mitigation is confirmed"))

    # Optional policy actions for operator workflow.
    out.append(_action_candidate("DISABLE_POLICY", "Disable an identified problematic policy", dangerous=True, requires_policy_id=True))
    out.append(_action_candidate("LOWER_POLICY_PRIORITY", "Reduce priority for unstable policy", requires_policy_id=True))
    out.append(_action_candidate("RESUME_SHADOW", "Keep router in shadow-only mode after stabilization"))
    out.append(_action_candidate("RUN_E2E_VALIDATION", "Run the scenario-router E2E validation script"))

    dedup: list[dict[str, Any]] = []
    seen: set[str] = set()
    for x in out:
        key = str(x.get("action_type") or "")
        if key in seen:
            continue
        seen.add(key)
        dedup.append(x)
    return dedup


def preview_scenario_router_incident_actions(
    *,
    mlops_db_path: str,
    alert_id: str | None = None,
    runbook_id: str | None = None,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    alert, runbook = _resolve_context(store=db_store, alert_id=alert_id, runbook_id=runbook_id)

    target = str((alert.get("target") if alert else runbook.get("target")) or "win")
    rollout = db_store.get_router_rollout(target=target, create_if_missing=False) or {}

    return {
        "target": target,
        "alert_id": str(alert.get("alert_id") or ""),
        "runbook_id": str(runbook.get("runbook_id") or ""),
        "alert": alert,
        "runbook": runbook,
        "rollout": rollout,
        "recommended_actions": _build_recommended_actions(alert=alert, runbook=runbook),
        "warnings": [],
    }


def _apply_rollout_action(*, store: MLOpsStore, target: str, next_status: str, decision: str, action: str, reason: str) -> dict[str, Any]:
    current = get_scenario_router_rollout_status(
        mlops_db_path=str(store.db_path),
        target=target,
        create_if_missing=True,
        store=store,
    )
    updated = store.update_router_rollout(
        rollout_id=str(current.get("rollout_id") or ""),
        target=target,
        current_percent=0,
        previous_percent=int(current.get("current_percent") or 0),
        router_mode="shadow",
        status=str(next_status),
        last_decision=str(decision),
        last_reason=str(reason),
    )
    store.insert_router_rollout_event(
        rollout_id=str((updated or {}).get("rollout_id") or current.get("rollout_id") or ""),
        target=target,
        from_percent=int(current.get("current_percent") or 0),
        to_percent=0,
        decision=str(decision),
        action=str(action),
        reason=str(reason),
        summary={},
    )
    return {
        "before": current,
        "after": updated or {},
    }


def execute_scenario_router_incident_action(
    *,
    mlops_db_path: str,
    race_db_path: str,
    action_type: str,
    alert_id: str | None = None,
    runbook_id: str | None = None,
    apply_updates: bool = False,
    confirm: bool = False,
    requested_by: str = "",
    approved_by: str = "",
    policy_id: str | None = None,
    priority_delta: int = 10,
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
    min_races: int = 30,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    at = str(action_type or "").strip().upper()
    alert, runbook = _resolve_context(store=db_store, alert_id=alert_id, runbook_id=runbook_id)

    aid = str(alert.get("alert_id") or "")
    rid = str(runbook.get("runbook_id") or "")
    target = str((alert.get("target") if alert else runbook.get("target")) or "win")

    if at in DANGEROUS_ACTIONS and not bool(confirm):
        action_id = db_store.insert_incident_action(
            alert_id=(aid or None),
            runbook_id=(rid or None),
            target=target,
            action_type=at,
            status="SKIPPED",
            dry_run=not bool(apply_updates),
            requested_by=str(requested_by or ""),
            approved_by=str(approved_by or ""),
            result={"rejected": True, "reason": "confirm=true is required for dangerous action"},
            error_message="dangerous action requires confirm=true",
            executed=False,
        )
        return {
            "success": False,
            "action_id": action_id,
            "status": "SKIPPED",
            "action_type": at,
            "error": "dangerous action requires confirm=true",
        }

    if aid and db_store.has_executed_incident_action(alert_id=aid, action_type=at):
        action_id = db_store.insert_incident_action(
            alert_id=aid,
            runbook_id=(rid or None),
            target=target,
            action_type=at,
            status="SKIPPED",
            dry_run=not bool(apply_updates),
            requested_by=str(requested_by or ""),
            approved_by=str(approved_by or ""),
            result={"deduplicated": True},
            error_message="already executed for this alert/action",
            executed=False,
        )
        return {
            "success": False,
            "action_id": action_id,
            "status": "SKIPPED",
            "action_type": at,
            "deduplicated": True,
        }

    is_dry = not bool(apply_updates)
    try:
        result: dict[str, Any] = {}

        if is_dry:
            result = {
                "dry_run": True,
                "action_type": at,
                "target": target,
                "message": "apply_updates=false: no state changes applied",
            }
        elif at == "STOP_CANARY":
            result = _apply_rollout_action(
                store=db_store,
                target=target,
                next_status="STOPPED",
                decision="STOP_CANARY",
                action="STOP",
                reason="incident action executor",
            )
        elif at == "ROLLBACK_TO_SHADOW":
            result = _apply_rollout_action(
                store=db_store,
                target=target,
                next_status="ROLLBACK",
                decision="ROLLBACK_TO_SHADOW",
                action="ROLLBACK",
                reason="incident action executor",
            )
        elif at == "RESUME_SHADOW":
            result = _apply_rollout_action(
                store=db_store,
                target=target,
                next_status="SHADOW_ONLY",
                decision="RESUME_SHADOW",
                action="RESUME",
                reason="incident action executor",
            )
        elif at == "RESOLVE_ALERT":
            if not aid:
                raise ValueError("RESOLVE_ALERT requires alert_id")
            res = resolve_scenario_router_alert(
                mlops_db_path=str(db_store.db_path),
                alert_id=aid,
                message="resolved by incident action executor",
                store=db_store,
            )
            if not bool(res.get("success")):
                raise ValueError(f"alert_id not found or already resolved: {aid}")
            result = res
        elif at == "RUN_CANARY_EVALUATE":
            result = evaluate_scenario_router_canary(
                mlops_db_path=str(db_store.db_path),
                race_db_path=str(race_db_path),
                target=target,
                date_from=(str(date_from) if date_from else None),
                date_to=(str(date_to) if date_to else None),
                min_races=int(min_races),
                stake_per_race=int(stake_per_race),
                store=db_store,
            )
        elif at == "RUN_ROUTER_BACKTEST":
            result = run_scenario_router_backtest(
                mlops_db_path=str(db_store.db_path),
                race_db_path=str(race_db_path),
                target=target,
                date_from=(str(date_from) if date_from else None),
                date_to=(str(date_to) if date_to else None),
                stake_per_race=int(stake_per_race),
            )
        elif at == "RUN_E2E_VALIDATION":
            result = {
                "manual_command": "python scripts/scenario_router_e2e_validation.py",
                "message": "E2E validation is prepared as manual operational command",
            }
        elif at == "DISABLE_POLICY":
            pid = str(policy_id or "").strip()
            if not pid:
                raise ValueError("DISABLE_POLICY requires policy_id")
            cur = db_store.get_scenario_model_policy_by_id(policy_id=pid)
            if not cur:
                raise ValueError(f"policy_id not found: {pid}")
            updated = db_store.update_scenario_model_policy(
                policy_id=pid,
                status="disabled",
                note_suffix="disabled by incident action executor",
            )
            result = {"before": cur, "after": updated}
        elif at == "LOWER_POLICY_PRIORITY":
            pid = str(policy_id or "").strip()
            if not pid:
                raise ValueError("LOWER_POLICY_PRIORITY requires policy_id")
            cur = db_store.get_scenario_model_policy_by_id(policy_id=pid)
            if not cur:
                raise ValueError(f"policy_id not found: {pid}")
            next_priority = max(1, int(cur.get("priority") or 100) - max(1, int(priority_delta)))
            updated = db_store.update_scenario_model_policy(
                policy_id=pid,
                priority=next_priority,
                note_suffix="priority lowered by incident action executor",
            )
            result = {"before": cur, "after": updated}
        else:
            raise ValueError(f"unsupported action_type: {at}")

        status = "DRY_RUN" if is_dry else "EXECUTED"
        action_id = db_store.insert_incident_action(
            alert_id=(aid or None),
            runbook_id=(rid or None),
            target=target,
            action_type=at,
            status=status,
            dry_run=is_dry,
            requested_by=str(requested_by or ""),
            approved_by=str(approved_by or ""),
            result=result,
            error_message=None,
            executed=(not is_dry),
        )
        return {
            "success": True,
            "action_id": action_id,
            "status": status,
            "action_type": at,
            "target": target,
            "result": result,
        }
    except Exception as e:
        action_id = db_store.insert_incident_action(
            alert_id=(aid or None),
            runbook_id=(rid or None),
            target=target,
            action_type=at,
            status="FAILED",
            dry_run=is_dry,
            requested_by=str(requested_by or ""),
            approved_by=str(approved_by or ""),
            result={},
            error_message=str(e),
            executed=False,
        )
        return {
            "success": False,
            "action_id": action_id,
            "status": "FAILED",
            "action_type": at,
            "target": target,
            "error": str(e),
        }
