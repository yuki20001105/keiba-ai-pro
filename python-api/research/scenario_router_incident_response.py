from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore
from research.scenario_router_incident_actions import preview_scenario_router_incident_actions
from research.scenario_router_notifications import test_scenario_router_notification_channel
from research.scenario_router_runbooks import generate_scenario_router_runbook


def prepare_scenario_router_incident_response(
    *,
    mlops_db_path: str,
    alert_id: str,
    save_response: bool = True,
    include_runbook_summary: bool = True,
    notification_channel_type: str = "slack",
    include_action_preview: bool = True,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    aid = str(alert_id or "").strip()
    if not aid:
        raise ValueError("alert_id is required")

    alert = db_store.get_router_alert_by_id(alert_id=aid)
    if not alert:
        raise ValueError(f"alert_id not found: {aid}")

    existing = db_store.list_router_runbooks(alert_id=aid, limit=1)
    if existing:
        runbook = existing[0]
        runbook_reused = True
    else:
        runbook = generate_scenario_router_runbook(
            mlops_db_path=str(db_store.db_path),
            alert_id=aid,
            include_notification_summary=True,
            save_runbook=True,
            store=db_store,
        )
        runbook_reused = False

    action_preview: dict[str, Any] = {}
    recommended_actions: list[dict[str, Any]] = []
    if bool(include_action_preview):
        action_preview = preview_scenario_router_incident_actions(
            mlops_db_path=str(db_store.db_path),
            alert_id=aid,
            runbook_id=(str(runbook.get("runbook_id") or "") or None),
            store=db_store,
        )
        recommended_actions = action_preview.get("recommended_actions") if isinstance(action_preview.get("recommended_actions"), list) else []

    notif = test_scenario_router_notification_channel(
        mlops_db_path=str(db_store.db_path),
        channel_type=str(notification_channel_type or "slack"),
        name="incident_response_prepare_preview",
        config={},
        payload={},
        alert_id=aid,
        include_runbook_summary=bool(include_runbook_summary),
        apply_send=False,
        store=db_store,
    )

    notification_preview = {
        "channel_type": str(notif.get("channel_type") or notification_channel_type or "slack"),
        "payload_preview": (notif.get("payload_preview") if isinstance(notif.get("payload_preview"), dict) else {}),
        "runbook_summary": (notif.get("runbook_summary") if isinstance(notif.get("runbook_summary"), dict) else {}),
    }

    summary = {
        "alert_title": str(alert.get("title") or ""),
        "alert_message": str(alert.get("message") or ""),
        "runbook_title": str(runbook.get("title") or ""),
        "runbook_summary": str(runbook.get("summary") or ""),
        "recommended_action_count": int(len(recommended_actions)),
    }

    response_id = ""
    if bool(save_response):
        response_id = db_store.insert_incident_response(
            alert_id=aid,
            runbook_id=(str(runbook.get("runbook_id") or "") or None),
            target=str(alert.get("target") or "win"),
            severity=str(alert.get("severity") or "WARNING"),
            status="PREPARED",
            recommended_actions=recommended_actions,
            notification_preview=notification_preview,
            summary=summary,
        )

    return {
        "response_id": response_id,
        "saved": bool(save_response),
        "status": "PREPARED",
        "target": str(alert.get("target") or "win"),
        "severity": str(alert.get("severity") or "WARNING"),
        "alert_id": aid,
        "runbook_id": str(runbook.get("runbook_id") or ""),
        "runbook_reused": bool(runbook_reused),
        "runbook": runbook,
        "action_preview": action_preview,
        "recommended_actions": recommended_actions,
        "notification_preview": notification_preview,
        "summary": summary,
    }
