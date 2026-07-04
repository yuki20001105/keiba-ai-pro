from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from mlops import MLOpsStore


_SEV_ORDER = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
_SEV_EMOJI = {"INFO": ":information_source:", "WARNING": ":warning:", "CRITICAL": ":rotating_light:"}


def _sev_ok(actual: str, minimum: str) -> bool:
    a = _SEV_ORDER.get(str(actual or "").upper(), 0)
    m = _SEV_ORDER.get(str(minimum or "INFO").upper(), 1)
    return a >= m


def _severity_allowed_by_channel(alert_sev: str, channel_filter: str) -> bool:
    s = str(channel_filter or "").strip()
    if not s:
        return True
    allowed = {x.strip().upper() for x in s.split(",") if x.strip()}
    if not allowed:
        return True
    return str(alert_sev or "").upper() in allowed


def _build_payload(alert: dict[str, Any]) -> dict[str, Any]:
    summary = alert.get("summary") if isinstance(alert.get("summary"), dict) else {}
    return {
        "alert_id": str(alert.get("alert_id") or ""),
        "severity": str(alert.get("severity") or ""),
        "alert_type": str(alert.get("alert_type") or ""),
        "title": str(alert.get("title") or ""),
        "message": str(alert.get("message") or ""),
        "target": str(alert.get("target") or ""),
        "source_run_id": str(alert.get("source_run_id") or ""),
        "decision": str(alert.get("decision") or ""),
        "action": str(alert.get("action") or ""),
        "summary": summary,
    }


def _summary_excerpt(summary: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "canary_active_races",
        "shadow_races",
        "roi_lift",
        "hit_rate_lift",
        "fallback_rate",
        "no_model_rate",
        "specialist_usage_rate",
    ]
    out: dict[str, Any] = {}
    for k in keys:
        if k in summary:
            out[k] = summary.get(k)
    return out


def _recommended_action(payload: dict[str, Any]) -> str:
    decision = str(payload.get("decision") or "")
    action = str(payload.get("action") or "")
    alert_type = str(payload.get("alert_type") or "")

    if decision == "STOP_CANARY" or action == "STOP":
        return "Immediately keep router_mode=shadow, inspect no_model/fallback and latest rollout runs."
    if decision == "ROLLBACK_TO_SHADOW" or action == "ROLLBACK":
        return "Rollback to shadow and review canary vs shadow metrics before next increase."
    if alert_type == "RUN_FAILED":
        return "Check rollout scheduler logs and rerun in dry-run mode first."
    return "Review alert details and rollout/alert APIs before applying updates."


def _format_slack_payload(base_payload: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
    cfg = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    sev = str(base_payload.get("severity") or "WARNING").upper()
    emoji = _SEV_EMOJI.get(sev, ":warning:")
    summary = base_payload.get("summary") if isinstance(base_payload.get("summary"), dict) else {}
    excerpt = _summary_excerpt(summary)
    rec = _recommended_action(base_payload)
    runbook_summary = base_payload.get("runbook_summary") if isinstance(base_payload.get("runbook_summary"), dict) else {}

    fields = [
        {"type": "mrkdwn", "text": f"*Target*\n{base_payload.get('target') or ''}"},
        {"type": "mrkdwn", "text": f"*Alert Type*\n{base_payload.get('alert_type') or ''}"},
        {"type": "mrkdwn", "text": f"*Decision*\n{base_payload.get('decision') or ''}"},
        {"type": "mrkdwn", "text": f"*Action*\n{base_payload.get('action') or ''}"},
        {"type": "mrkdwn", "text": f"*Run*\n{base_payload.get('source_run_id') or ''}"},
    ]

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} [{sev}] {base_payload.get('title') or 'Scenario Router Alert'}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": str(base_payload.get("message") or "")},
        },
        {
            "type": "section",
            "fields": fields,
        },
    ]

    if excerpt:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Summary*\n```{excerpt}```",
                },
            }
        )
    if runbook_summary:
        rb_actions = runbook_summary.get("recommended_actions") if isinstance(runbook_summary.get("recommended_actions"), list) else []
        rb_recovery = runbook_summary.get("recovery_conditions") if isinstance(runbook_summary.get("recovery_conditions"), list) else []
        rb_lines = [
            f"*Runbook*\\n{str(runbook_summary.get('title') or '')}",
            f"{str(runbook_summary.get('summary') or '')}",
        ]
        if rb_actions:
            rb_lines.append("Initial Actions: " + " | ".join([str(x) for x in rb_actions[:3]]))
        if rb_recovery:
            rb_lines.append("Recovery: " + " | ".join([str(x) for x in rb_recovery[:3]]))
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\\n".join(rb_lines)},
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Recommended Action*\n{rec}"},
        }
    )

    return {
        "username": str(cfg.get("username") or "Scenario Router"),
        "icon_emoji": str(cfg.get("icon_emoji") or ":horse_racing:"),
        "text": f"{emoji} [{sev}] {base_payload.get('title') or 'Scenario Router Alert'}",
        "blocks": blocks,
    }


def _format_notion_payload(base_payload: dict[str, Any], channel: dict[str, Any]) -> dict[str, Any]:
    cfg = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    sev = str(base_payload.get("severity") or "WARNING").upper()
    summary = base_payload.get("summary") if isinstance(base_payload.get("summary"), dict) else {}
    excerpt = _summary_excerpt(summary)
    rec = _recommended_action(base_payload)
    runbook_summary = base_payload.get("runbook_summary") if isinstance(base_payload.get("runbook_summary"), dict) else {}

    return {
        "title": str(base_payload.get("title") or "Scenario Router Alert"),
        "database_id": str(cfg.get("database_id") or ""),
        "properties": {
            "severity": sev,
            "target": str(base_payload.get("target") or ""),
            "alert_type": str(base_payload.get("alert_type") or ""),
            "decision": str(base_payload.get("decision") or ""),
            "action": str(base_payload.get("action") or ""),
            "source_run_id": str(base_payload.get("source_run_id") or ""),
            "alert_id": str(base_payload.get("alert_id") or ""),
        },
        "body": {
            "message": str(base_payload.get("message") or ""),
            "summary": excerpt,
            "recommended_action": rec,
            "runbook_summary": runbook_summary,
        },
    }


def _build_channel_payload(*, channel: dict[str, Any], base_payload: dict[str, Any]) -> dict[str, Any]:
    ctype = str(channel.get("channel_type") or "webhook").lower()
    if ctype == "slack":
        return _format_slack_payload(base_payload, channel)
    if ctype == "notion":
        return _format_notion_payload(base_payload, channel)
    return dict(base_payload)


def _attach_runbook_summary_if_enabled(
    *,
    store: MLOpsStore,
    channel: dict[str, Any],
    alert_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    cfg = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    if not bool(cfg.get("include_runbook_summary", False)):
        return payload
    rb = _resolve_runbook_for_alert(store=store, alert_id=alert_id)
    if not rb:
        return payload
    rb_summary = _to_runbook_summary(rb)

    out = dict(payload)
    out["runbook_summary"] = rb_summary
    out["runbook"] = rb_summary
    return out


def _to_runbook_summary(runbook: dict[str, Any]) -> dict[str, Any]:
    checklist = runbook.get("checklist") if isinstance(runbook.get("checklist"), list) else []
    actions = runbook.get("recommended_actions") if isinstance(runbook.get("recommended_actions"), list) else []
    recovery = runbook.get("recovery_conditions") if isinstance(runbook.get("recovery_conditions"), list) else []
    observed = runbook.get("observed_metrics") if isinstance(runbook.get("observed_metrics"), dict) else {}
    threshold_cmp = runbook.get("threshold_comparison") if isinstance(runbook.get("threshold_comparison"), list) else []

    return {
        "runbook_id": str(runbook.get("runbook_id") or ""),
        "title": str(runbook.get("title") or ""),
        "summary": str(runbook.get("summary") or ""),
        "initial_actions": [str(x) for x in checklist[:3]],
        "recommended_actions": [str(x) for x in actions[:5]],
        "recovery_conditions": [str(x) for x in recovery[:5]],
        "observed_metrics": observed,
        "threshold_comparison": threshold_cmp,
    }


def _resolve_runbook_for_alert(*, store: MLOpsStore, alert_id: str) -> dict[str, Any] | None:
    aid = str(alert_id or "").strip()
    if not aid:
        return None
    items = store.list_router_runbooks(alert_id=aid, limit=1)
    if items:
        return items[0]
    from research.scenario_router_runbooks import generate_scenario_router_runbook  # local import to avoid package init cycle

    return generate_scenario_router_runbook(
        mlops_db_path=str(store.db_path),
        alert_id=aid,
        include_notification_summary=False,
        save_runbook=False,
        store=store,
    )


def _resolve_channel_url(channel: dict[str, Any]) -> tuple[str, str]:
    cfg = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    url = str(cfg.get("url") or "").strip()
    if url:
        return url, ""
    env_name = str(cfg.get("url_env") or "").strip()
    if not env_name:
        return "", "webhook url/url_env is missing"
    val = str(os.getenv(env_name) or "").strip()
    if not val:
        return "", f"environment variable not set: {env_name}"
    return val, ""


def _send_generic_webhook(*, channel: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str]:
    cfg = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    url, url_err = _resolve_channel_url(channel)
    if not url:
        return False, url_err

    method = str(cfg.get("method") or "POST").upper()
    headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    token_env = str(cfg.get("token_env") or "").strip()
    if token_env:
        token_val = str(os.getenv(token_env) or "").strip()
        if token_val and "Authorization" not in headers:
            headers = {**headers, "Authorization": f"Bearer {token_val}"}
    timeout_sec = float(cfg.get("timeout_sec") or 10.0)

    try:
        with httpx.Client(timeout=timeout_sec) as c:
            resp = c.request(method, url, json=payload, headers=headers)
            if 200 <= int(resp.status_code) < 300:
                return True, ""
            return False, f"http {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def dispatch_scenario_router_notifications(
    *,
    mlops_db_path: str,
    target: str | None = None,
    severity_min: str = "WARNING",
    channel_types: list[str] | None = None,
    apply_send: bool = False,
    limit: int = 50,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")

    alerts = db_store.list_router_alerts(target=tgt, status="open", limit=max(1, int(limit)))
    alerts = [a for a in alerts if _sev_ok(str(a.get("severity") or ""), str(severity_min or "INFO"))]

    channels = db_store.list_router_notification_channels(
        enabled_only=False,
        channel_types=channel_types,
        limit=200,
    )

    planned = 0
    sent = 0
    failed = 0
    skipped = 0
    deduped = 0
    deliveries: list[dict[str, Any]] = []

    for alert in alerts:
        alert_id = str(alert.get("alert_id") or "")
        sev = str(alert.get("severity") or "").upper()
        payload = _build_payload(alert)

        for ch in channels:
            channel_id = str(ch.get("channel_id") or "")
            channel_type = str(ch.get("channel_type") or "")
            if not channel_id:
                continue

            base_payload = _build_payload(alert)
            payload = _build_channel_payload(channel=ch, base_payload=base_payload)
            payload = _attach_runbook_summary_if_enabled(
                store=db_store,
                channel=ch,
                alert_id=alert_id,
                payload=payload,
            )

            if not bool(ch.get("enabled")):
                did = db_store.insert_router_notification_delivery(
                    alert_id=alert_id,
                    channel_id=channel_id,
                    status="skipped",
                    attempt_count=1,
                    last_error="channel disabled",
                    payload=payload,
                    sent=False,
                )
                skipped += 1
                deliveries.append({"delivery_id": did, "alert_id": alert_id, "channel_id": channel_id, "status": "skipped"})
                continue

            if not _severity_allowed_by_channel(sev, str(ch.get("severity_filter") or "")):
                did = db_store.insert_router_notification_delivery(
                    alert_id=alert_id,
                    channel_id=channel_id,
                    status="skipped",
                    attempt_count=1,
                    last_error="severity filtered",
                    payload=payload,
                    sent=False,
                )
                skipped += 1
                deliveries.append({"delivery_id": did, "alert_id": alert_id, "channel_id": channel_id, "status": "skipped"})
                continue

            if db_store.has_sent_notification_delivery(alert_id=alert_id, channel_id=channel_id):
                did = db_store.insert_router_notification_delivery(
                    alert_id=alert_id,
                    channel_id=channel_id,
                    status="skipped",
                    attempt_count=1,
                    last_error="already sent",
                    payload=payload,
                    sent=False,
                )
                deduped += 1
                deliveries.append({"delivery_id": did, "alert_id": alert_id, "channel_id": channel_id, "status": "skipped"})
                continue

            planned += 1
            if not bool(apply_send):
                deliveries.append({"delivery_id": "", "alert_id": alert_id, "channel_id": channel_id, "status": "planned"})
                continue

            ok = False
            err = "unsupported channel"
            if channel_type in {"webhook", "slack", "notion"}:
                ok, err = _send_generic_webhook(channel=ch, payload=payload)
            elif channel_type == "console":
                ok, err = True, ""

            if ok:
                did = db_store.insert_router_notification_delivery(
                    alert_id=alert_id,
                    channel_id=channel_id,
                    status="sent",
                    attempt_count=1,
                    last_error=None,
                    payload=payload,
                    sent=True,
                )
                sent += 1
                deliveries.append({"delivery_id": did, "alert_id": alert_id, "channel_id": channel_id, "status": "sent"})
            else:
                did = db_store.insert_router_notification_delivery(
                    alert_id=alert_id,
                    channel_id=channel_id,
                    status="failed",
                    attempt_count=1,
                    last_error=str(err or "send failed"),
                    payload=payload,
                    sent=False,
                )
                failed += 1
                deliveries.append({"delivery_id": did, "alert_id": alert_id, "channel_id": channel_id, "status": "failed"})

    return {
        "target": tgt,
        "apply_send": bool(apply_send),
        "severity_min": str(severity_min or "INFO").upper(),
        "alert_count": int(len(alerts)),
        "channel_count": int(len(channels)),
        "planned": int(planned),
        "sent": int(sent),
        "failed": int(failed),
        "skipped": int(skipped),
        "deduped": int(deduped),
        "deliveries": deliveries,
    }


def test_scenario_router_notification_channel(
    *,
    mlops_db_path: str,
    channel_type: str,
    name: str,
    config: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    alert_id: str | None = None,
    include_runbook_summary: bool = False,
    apply_send: bool = False,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    ch = {
        "channel_type": str(channel_type or "webhook"),
        "name": str(name or "test_channel"),
        "config": dict(config or {}),
        "enabled": True,
    }
    aid = str(alert_id or "").strip()
    if aid:
        alert = db_store.get_router_alert_by_id(alert_id=aid)
        if not alert:
            raise ValueError(f"alert_id not found: {aid}")
        base_payload = _build_payload(alert)
    else:
        base_payload = dict(payload or {
            "alert_id": "test_alert",
            "severity": "WARNING",
            "alert_type": "TEST",
            "title": "Scenario Router Notification Test",
            "message": "test message",
            "target": "win",
            "source_run_id": "test_run",
            "decision": "",
            "action": "",
            "summary": {},
        })

    rb_summary: dict[str, Any] = {}
    if bool(include_runbook_summary):
        if not aid:
            raise ValueError("alert_id is required when include_runbook_summary=true")
        rb = _resolve_runbook_for_alert(store=db_store, alert_id=aid)
        if rb:
            rb_summary = _to_runbook_summary(rb)
            base_payload = dict(base_payload)
            base_payload["runbook_summary"] = rb_summary

    p = _build_channel_payload(channel=ch, base_payload=base_payload)
    return {
        "success": True,
        "apply_send": False,
        "channel_type": ch["channel_type"],
        "alert_id": aid,
        "include_runbook_summary": bool(include_runbook_summary),
        "runbook_summary": rb_summary,
        "payload_preview": p,
        "payload": p,
        "note": (
            "test endpoint is preview-only; external send is disabled"
            if bool(apply_send)
            else ""
        ),
    }
