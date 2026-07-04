from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore
from research.scenario_router_incident_actions import execute_scenario_router_incident_action
from research.scenario_router_incident_response import prepare_scenario_router_incident_response
from research.scenario_router_notifications import dispatch_scenario_router_notifications


SAFE_ACTIONS = {
    "RUN_CANARY_EVALUATE",
    "RUN_ROUTER_BACKTEST",
    "RUN_E2E_VALIDATION",
    "NOTIFICATION_DISPATCH",
    "RESOLVE_ALERT",
}
DANGEROUS_ACTIONS = {
    "STOP_CANARY",
    "ROLLBACK_TO_SHADOW",
    "DISABLE_POLICY",
    "LOWER_POLICY_PRIORITY",
}


def _normalize_action_types(response: dict[str, Any]) -> list[str]:
    rec = response.get("recommended_actions") if isinstance(response.get("recommended_actions"), list) else []
    out: list[str] = []
    seen: set[str] = set()
    for r in rec:
        if not isinstance(r, dict):
            continue
        at = str(r.get("action_type") or "").strip().upper()
        if not at or at in seen:
            continue
        seen.add(at)
        out.append(at)

    # Always include notification dispatch in auto-recovery plan.
    if "NOTIFICATION_DISPATCH" not in seen:
        out.append("NOTIFICATION_DISPATCH")
    return out


def _policy_score(policy: dict[str, Any], *, alert_type: str, severity: str) -> int:
    score = 0
    p_alert = str(policy.get("alert_type") or "*")
    p_sev = str(policy.get("severity") or "*")
    if p_alert == alert_type:
        score += 2
    elif p_alert == "*":
        score += 0
    else:
        return -1
    if p_sev == severity:
        score += 1
    elif p_sev == "*":
        score += 0
    else:
        return -1
    return score


def _resolve_policy_for_action(
    *,
    policies: list[dict[str, Any]],
    alert_type: str,
    severity: str,
    action_type: str,
) -> dict[str, Any] | None:
    cand = [p for p in policies if str(p.get("action_type") or "").upper() == action_type]
    best: dict[str, Any] | None = None
    best_score = -1
    for p in cand:
        sc = _policy_score(p, alert_type=alert_type, severity=severity)
        if sc > best_score:
            best_score = sc
            best = p
    return best if best_score >= 0 else None


def _default_auto_recovery_policies() -> list[dict[str, Any]]:
    return [
        {"policy_id": "default_run_canary_eval", "alert_type": "*", "severity": "*", "action_type": "RUN_CANARY_EVALUATE", "auto_execute": True, "require_confirm": False, "enabled": True},
        {"policy_id": "default_run_backtest", "alert_type": "*", "severity": "*", "action_type": "RUN_ROUTER_BACKTEST", "auto_execute": True, "require_confirm": False, "enabled": True},
        {"policy_id": "default_run_e2e", "alert_type": "*", "severity": "*", "action_type": "RUN_E2E_VALIDATION", "auto_execute": True, "require_confirm": False, "enabled": True},
        {"policy_id": "default_notify", "alert_type": "*", "severity": "*", "action_type": "NOTIFICATION_DISPATCH", "auto_execute": True, "require_confirm": False, "enabled": True},
        {"policy_id": "default_resolve_warn", "alert_type": "*", "severity": "WARNING", "action_type": "RESOLVE_ALERT", "auto_execute": True, "require_confirm": False, "enabled": True},
        {"policy_id": "default_resolve_info", "alert_type": "*", "severity": "INFO", "action_type": "RESOLVE_ALERT", "auto_execute": True, "require_confirm": False, "enabled": True},
    ]


def _resolve_response_package(
    *,
    store: MLOpsStore,
    response_id: str | None,
    alert_id: str | None,
    include_action_preview: bool,
    include_runbook_summary: bool,
    notification_channel_type: str,
) -> dict[str, Any]:
    rid = str(response_id or "").strip()
    aid = str(alert_id or "").strip()

    if rid:
        resp = store.get_incident_response_by_id(response_id=rid)
        if not resp:
            raise ValueError(f"response_id not found: {rid}")
        return resp

    if not aid:
        raise ValueError("response_id or alert_id is required")

    listed = store.list_incident_responses(alert_id=aid, limit=1)
    if listed:
        return listed[0]

    prepared = prepare_scenario_router_incident_response(
        mlops_db_path=str(store.db_path),
        alert_id=aid,
        save_response=True,
        include_runbook_summary=bool(include_runbook_summary),
        notification_channel_type=str(notification_channel_type or "slack"),
        include_action_preview=bool(include_action_preview),
        store=store,
    )
    rid2 = str(prepared.get("response_id") or "")
    if rid2:
        item = store.get_incident_response_by_id(response_id=rid2)
        if item:
            return item
    return prepared


def evaluate_scenario_router_auto_recovery(
    *,
    mlops_db_path: str,
    response_id: str | None = None,
    alert_id: str | None = None,
    include_action_preview: bool = True,
    include_runbook_summary: bool = True,
    notification_channel_type: str = "slack",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))

    resp = _resolve_response_package(
        store=db_store,
        response_id=response_id,
        alert_id=alert_id,
        include_action_preview=bool(include_action_preview),
        include_runbook_summary=bool(include_runbook_summary),
        notification_channel_type=str(notification_channel_type or "slack"),
    )

    aid = str(resp.get("alert_id") or alert_id or "")
    alert = db_store.get_router_alert_by_id(alert_id=aid)
    if not alert:
        raise ValueError(f"alert_id not found: {aid}")

    alert_type = str(alert.get("alert_type") or "").upper()
    severity = str(alert.get("severity") or "WARNING").upper()

    actions = _normalize_action_types(resp)
    user_policies = db_store.list_auto_recovery_policies(enabled_only=True, limit=1000)
    policies = [*user_policies, *_default_auto_recovery_policies()]

    plan: list[dict[str, Any]] = []
    for at in actions:
        pol = _resolve_policy_for_action(
            policies=policies,
            alert_type=alert_type,
            severity=severity,
            action_type=at,
        )

        auto_execute = bool(pol.get("auto_execute")) if pol else False
        require_confirm = bool(pol.get("require_confirm")) if pol else False
        manual_required = False
        blocked = False
        reason = ""

        if at in DANGEROUS_ACTIONS:
            auto_execute = False
            manual_required = True
            require_confirm = True
            reason = "dangerous action requires manual approval"
        elif at == "RESOLVE_ALERT" and severity not in {"INFO", "WARNING"}:
            auto_execute = False
            manual_required = True
            reason = "auto resolve is limited to INFO/WARNING severity"
        elif not pol:
            auto_execute = False
            manual_required = True
            reason = "policy not defined"
        elif require_confirm:
            auto_execute = False
            manual_required = True
            reason = "policy requires confirm"
        elif at not in SAFE_ACTIONS and not manual_required:
            auto_execute = False
            manual_required = True
            reason = "action is not classified as safe"

        plan.append(
            {
                "action_type": at,
                "auto_execute": bool(auto_execute),
                "manual_required": bool(manual_required),
                "blocked": bool(blocked),
                "require_confirm": bool(require_confirm),
                "policy_id": (str(pol.get("policy_id") or "") if pol else ""),
                "reason": str(reason),
            }
        )

    return {
        "response_id": str(resp.get("response_id") or ""),
        "alert_id": aid,
        "target": str(alert.get("target") or "win"),
        "severity": severity,
        "alert_type": alert_type,
        "plan": plan,
        "warnings": [],
    }


def execute_scenario_router_auto_recovery(
    *,
    mlops_db_path: str,
    race_db_path: str,
    response_id: str | None = None,
    alert_id: str | None = None,
    apply_updates: bool = False,
    confirm: bool = False,
    requested_by: str = "",
    approved_by: str = "",
    include_action_preview: bool = True,
    include_runbook_summary: bool = True,
    notification_channel_type: str = "slack",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    eval_data = evaluate_scenario_router_auto_recovery(
        mlops_db_path=str(db_store.db_path),
        response_id=response_id,
        alert_id=alert_id,
        include_action_preview=bool(include_action_preview),
        include_runbook_summary=bool(include_runbook_summary),
        notification_channel_type=str(notification_channel_type or "slack"),
        store=db_store,
    )

    rid = str(eval_data.get("response_id") or "")
    aid = str(eval_data.get("alert_id") or "")
    target = str(eval_data.get("target") or "win")

    out_rows: list[dict[str, Any]] = []
    for step in (eval_data.get("plan") or []):
        if not isinstance(step, dict):
            continue
        at = str(step.get("action_type") or "")
        auto_execute = bool(step.get("auto_execute"))
        manual_required = bool(step.get("manual_required"))
        blocked = bool(step.get("blocked"))

        if blocked:
            eid = db_store.insert_auto_recovery_execution(
                response_id=(rid or None),
                alert_id=(aid or None),
                action_type=at,
                status="BLOCKED",
                auto_executed=False,
                manual_required=manual_required,
                result={},
                error_message=str(step.get("reason") or "blocked by policy"),
                executed=False,
            )
            out_rows.append({"execution_id": eid, "action_type": at, "status": "BLOCKED", "success": False})
            continue

        if not bool(apply_updates):
            eid = db_store.insert_auto_recovery_execution(
                response_id=(rid or None),
                alert_id=(aid or None),
                action_type=at,
                status="DRY_RUN",
                auto_executed=False,
                manual_required=manual_required,
                result={"would_execute": bool(auto_execute and not manual_required)},
                error_message=None,
                executed=False,
            )
            out_rows.append({"execution_id": eid, "action_type": at, "status": "DRY_RUN", "success": True})
            continue

        if manual_required or not auto_execute:
            eid = db_store.insert_auto_recovery_execution(
                response_id=(rid or None),
                alert_id=(aid or None),
                action_type=at,
                status="SKIPPED",
                auto_executed=False,
                manual_required=True,
                result={"manual_required": True},
                error_message="manual approval required",
                executed=False,
            )
            out_rows.append({"execution_id": eid, "action_type": at, "status": "SKIPPED", "success": False})
            continue

        if at == "NOTIFICATION_DISPATCH":
            res = dispatch_scenario_router_notifications(
                mlops_db_path=str(db_store.db_path),
                target=target,
                severity_min="INFO",
                channel_types=[],
                apply_send=True,
                limit=50,
                store=db_store,
            )
            eid = db_store.insert_auto_recovery_execution(
                response_id=(rid or None),
                alert_id=(aid or None),
                action_type=at,
                status="EXECUTED",
                auto_executed=True,
                manual_required=False,
                result=res,
                error_message=None,
                executed=True,
            )
            out_rows.append({"execution_id": eid, "action_type": at, "status": "EXECUTED", "success": True})
            continue

        act_res = execute_scenario_router_incident_action(
            mlops_db_path=str(db_store.db_path),
            race_db_path=str(race_db_path),
            action_type=at,
            alert_id=(aid or None),
            runbook_id=None,
            apply_updates=True,
            confirm=bool(confirm),
            requested_by=str(requested_by or ""),
            approved_by=str(approved_by or ""),
            store=db_store,
        )
        ok = bool(act_res.get("success"))
        st = "EXECUTED" if ok else "FAILED"
        eid = db_store.insert_auto_recovery_execution(
            response_id=(rid or None),
            alert_id=(aid or None),
            action_type=at,
            status=st,
            auto_executed=True,
            manual_required=False,
            result=act_res,
            error_message=(None if ok else str(act_res.get("error") or "auto recovery execution failed")),
            executed=ok,
        )
        out_rows.append({"execution_id": eid, "action_type": at, "status": st, "success": ok})

    return {
        "response_id": rid,
        "alert_id": aid,
        "target": target,
        "apply_updates": bool(apply_updates),
        "results": out_rows,
    }
