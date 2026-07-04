from __future__ import annotations

import json
import csv
import io
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from mlops import MLOpsStore  # type: ignore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _reports_dir(path: str | None = None) -> Path:
    if path and str(path).strip():
        return Path(str(path).strip())
    return _repo_root() / "reports"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_limit(limit: Any, default: int = 10, max_value: int = 100) -> int:
    n = _safe_int(limit, default)
    if n <= 0:
        return int(default)
    return max(1, min(int(n), int(max_value)))


def _normalize_refresh(refresh: Any, default: int = 0, max_value: int = 3600) -> int:
    n = _safe_int(refresh, default)
    if n <= 0:
        return 0
    return max(0, min(int(n), int(max_value)))


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_target_filter(target: str | None) -> tuple[str, str | None]:
    raw = str(target or "").strip()
    if not raw:
        return "win", "win"
    low = raw.lower()
    if low in {"all", "*"}:
        return raw, None
    return raw, raw


def _parse_datetime_like(value: Any) -> tuple[datetime | None, bool]:
    s = str(value or "").strip()
    if not s:
        return None, False
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s), False
    except Exception:
        return None, True


def _normalize_timeline_entity_type(entity_type: Any) -> tuple[str, bool]:
    raw = str(entity_type or "").strip().lower()
    if not raw or raw in {"all", "*"}:
        return "all", False
    mapping = {
        "alert": "alert",
        "runbook": "runbook",
        "response": "response",
        "incident_response": "response",
        "action": "action",
        "incident_action": "action",
        "auto_recovery_execution": "auto_recovery_execution",
        "notification_delivery": "notification_delivery",
    }
    val = mapping.get(raw)
    if not val:
        return "all", True
    return val, False


def _normalize_timeline_sort(value: Any) -> tuple[str, bool]:
    raw = str(value or "").strip().lower()
    if not raw:
        return "desc", False
    if raw not in {"asc", "desc"}:
        return "desc", True
    return raw, False


def _normalize_timeline_filters(
    *,
    entity_type: Any,
    status: Any,
    since: Any,
    until: Any,
    limit: Any,
    offset: Any,
    sort: Any,
) -> dict[str, Any]:
    invalid: list[str] = []
    et, bad_et = _normalize_timeline_entity_type(entity_type)
    if bad_et:
        invalid.append("entity_type")
    sr, bad_sort = _normalize_timeline_sort(sort)
    if bad_sort:
        invalid.append("sort")
    since_dt, bad_since = _parse_datetime_like(since)
    if bad_since:
        invalid.append("since")
    until_dt, bad_until = _parse_datetime_like(until)
    if bad_until:
        invalid.append("until")
    n = _normalize_limit(limit, default=50, max_value=200)
    off = max(0, _safe_int(offset, 0))
    return {
        "entity_type": et,
        "status": str(status or "").strip(),
        "since": str(since or "").strip(),
        "until": str(until or "").strip(),
        "sort": sr,
        "limit": n,
        "offset": off,
        "since_dt": since_dt,
        "until_dt": until_dt,
        "invalid": invalid,
    }


def _filter_and_paginate_timeline_items(items: list[dict[str, Any]], *, filters: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(filters.get("entity_type") or "all")
    entity_internal_map = {
        "all": "all",
        "alert": "alert",
        "runbook": "runbook",
        "response": "incident_response",
        "action": "incident_action",
        "auto_recovery_execution": "auto_recovery_execution",
        "notification_delivery": "notification_delivery",
    }
    entity_internal = str(entity_internal_map.get(entity_type, "all"))
    status = str(filters.get("status") or "").strip().lower()
    since_dt = filters.get("since_dt")
    until_dt = filters.get("until_dt")
    sort = str(filters.get("sort") or "desc")
    n = _safe_int(filters.get("limit"), 50)
    off = max(0, _safe_int(filters.get("offset"), 0))

    filtered: list[dict[str, Any]] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        et = str(x.get("entity_type") or "")
        st = str(x.get("status") or "").strip().lower()
        if entity_internal != "all" and et != entity_internal:
            continue
        if status and st != status:
            continue
        ts_raw = str(x.get("timestamp") or "").strip()
        if since_dt is not None or until_dt is not None:
            ts_dt, bad_ts = _parse_datetime_like(ts_raw)
            if bad_ts or ts_dt is None:
                continue
            if since_dt is not None and ts_dt < since_dt:
                continue
            if until_dt is not None and ts_dt > until_dt:
                continue
        filtered.append(x)

    filtered.sort(
        key=lambda x: (str(x.get("timestamp") or ""), str(x.get("entity_type") or ""), str(x.get("entity_id") or "")),
        reverse=(sort == "desc"),
    )

    returned = filtered[off : off + n]
    total = len(filtered)
    has_more = (off + n) < total
    pagination = {
        "limit": n,
        "offset": off,
        "returned": len(returned),
        "has_more": has_more,
        "next_offset": (off + n) if has_more else off,
    }
    return {
        "filtered": filtered,
        "returned": returned,
        "pagination": pagination,
    }


def _timeline_filters_out(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": str(filters.get("entity_type") or "all"),
        "status": str(filters.get("status") or ""),
        "since": str(filters.get("since") or ""),
        "until": str(filters.get("until") or ""),
        "sort": str(filters.get("sort") or "desc"),
        "invalid": list(filters.get("invalid") or []),
    }


def _csv_safe_cell(value: Any) -> str:
    s = str(value if value is not None else "")
    if s[:1] in {"=", "+", "-", "@"}:
        return "'" + s
    return s


def _markdown_cell(value: Any) -> str:
    s = str(value if value is not None else "")
    s = s.replace("\\", "\\\\")
    s = s.replace("|", "\\|")
    s = s.replace("\n", " ").replace("\r", " ")
    return s


def get_scenario_router_ops_timeline_items(
    *,
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    filters = _normalize_timeline_filters(
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    target_display, target_filter = _normalize_target_filter(target)
    s = store or MLOpsStore()

    # Read-only aggregation over latest alerts to avoid DB mutations and keep predictable load.
    alerts = s.list_router_alerts(target=target_filter, status=None, limit=200)
    all_items: list[dict[str, Any]] = []
    alert_ids: list[str] = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("alert_id") or "")
        if not aid:
            continue
        alert_ids.append(aid)
        chain = get_scenario_router_incident_timeline(
            alert_id=aid,
            target=target_display,
            entity_type=str(filters.get("entity_type") or "all"),
            status=str(filters.get("status") or ""),
            since=str(filters.get("since") or ""),
            until=str(filters.get("until") or ""),
            limit=200,
            offset=0,
            sort=str(filters.get("sort") or "desc"),
            store=s,
        )
        for x in (chain.get("items") if isinstance(chain.get("items"), list) else []):
            if not isinstance(x, dict):
                continue
            all_items.append(x)

    paged = _filter_and_paginate_timeline_items(all_items, filters=filters)
    filtered_all = paged.get("filtered") if isinstance(paged.get("filtered"), list) else []
    returned_items = paged.get("returned") if isinstance(paged.get("returned"), list) else []
    pagination = paged.get("pagination") if isinstance(paged.get("pagination"), dict) else {}

    counts_by_entity: dict[str, int] = {}
    for x in filtered_all:
        et = str(x.get("entity_type") or "")
        if not et:
            continue
        counts_by_entity[et] = int(counts_by_entity.get(et, 0)) + 1

    latest = filtered_all[0] if filtered_all else {}
    return {
        "summary": {
            "alert_id": "",
            "target": target_display,
            "total_events": len(filtered_all),
            "alert_count": len(alert_ids),
            "latest_timestamp": str(latest.get("timestamp") or ""),
            "latest_entity_type": str(latest.get("entity_type") or ""),
            "latest_status": str(latest.get("status") or ""),
            "counts_by_entity": counts_by_entity,
        },
        "filters": _timeline_filters_out(filters),
        "pagination": pagination,
        "items": returned_items,
    }


def build_timeline_export_markdown(*, timeline: dict[str, Any], generated_at: str) -> str:
    summary = timeline.get("summary") if isinstance(timeline.get("summary"), dict) else {}
    filters = timeline.get("filters") if isinstance(timeline.get("filters"), dict) else {}
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []

    lines = [
        "# Scenario Router Incident Timeline",
        "",
        "## Summary",
        f"- alert_id: {_markdown_cell(summary.get('alert_id') or '')}",
        f"- total_events: {_markdown_cell(summary.get('total_events') or 0)}",
        f"- filters: {_markdown_cell(json.dumps(filters, ensure_ascii=False, default=str))}",
        f"- generated_at: {_markdown_cell(generated_at)}",
        "",
        "## Timeline",
        "| timestamp | entity_type | entity_id | status | severity | action_type | summary |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in items:
        if not isinstance(x, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(x.get("timestamp") or ""),
                    _markdown_cell(x.get("entity_type") or ""),
                    _markdown_cell(x.get("entity_id") or ""),
                    _markdown_cell(x.get("status") or ""),
                    _markdown_cell(x.get("severity") or ""),
                    _markdown_cell(x.get("action_type") or ""),
                    _markdown_cell(x.get("summary") or ""),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Read-only Notice",
            "This export is read-only. Dangerous operations are intentionally not included.",
            "",
        ]
    )
    return "\n".join(lines)


def build_timeline_export_csv(*, timeline: dict[str, Any]) -> str:
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    headers = [
        "timestamp",
        "entity_type",
        "entity_id",
        "status",
        "severity",
        "action_type",
        "summary",
        "detail_url",
        "is_dangerous",
        "read_only_note",
    ]
    w.writerow(headers)
    for x in items:
        if not isinstance(x, dict):
            continue
        w.writerow(
            [
                _csv_safe_cell(x.get("timestamp") or ""),
                _csv_safe_cell(x.get("entity_type") or ""),
                _csv_safe_cell(x.get("entity_id") or ""),
                _csv_safe_cell(x.get("status") or ""),
                _csv_safe_cell(x.get("severity") or ""),
                _csv_safe_cell(x.get("action_type") or ""),
                _csv_safe_cell(x.get("summary") or ""),
                _csv_safe_cell(x.get("detail_url") or ""),
                _csv_safe_cell(bool(x.get("is_dangerous"))),
                _csv_safe_cell(x.get("read_only_note") or ""),
            ]
        )
    return out.getvalue()


def _read_json_file(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    meta = {
        "path": str(path),
        "missing": False,
        "decode_error": False,
        "error": "",
    }
    if not path.exists():
        meta["missing"] = True
        return {}, meta
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, meta
        meta["decode_error"] = True
        meta["error"] = "json root is not object"
        return {}, meta
    except Exception as e:
        meta["decode_error"] = True
        meta["error"] = str(e)
        return {}, meta


def _read_jsonl_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = {
        "path": str(path),
        "missing": False,
        "decode_error": False,
        "error": "",
    }
    if not path.exists():
        meta["missing"] = True
        return [], meta

    rows: list[dict[str, Any]] = []
    try:
        for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = ln.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
        return rows, meta
    except Exception as e:
        meta["decode_error"] = True
        meta["error"] = str(e)
        return [], meta


def _read_text_file(path: Path) -> tuple[str, dict[str, Any]]:
    meta = {
        "path": str(path),
        "missing": False,
        "decode_error": False,
        "error": "",
    }
    if not path.exists():
        meta["missing"] = True
        return "", meta
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace"), meta
    except Exception as e:
        meta["decode_error"] = True
        meta["error"] = str(e)
        return "", meta


def _compute_last_10_success_rate(history: list[dict[str, Any]]) -> float:
    last = history[-10:]
    if not last:
        return 0.0
    ok = sum(1 for r in last if str(r.get("overall_status") or "") == "PASS")
    return ok / float(len(last))


def _build_audit_summary(
    *,
    result: dict[str, Any],
    gate: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    result_summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    triage = result.get("triage") if isinstance(result.get("triage"), dict) else {}
    trend = result.get("trend") if isinstance(result.get("trend"), dict) else {}

    gate_status = str(gate.get("gate_status") or "")
    latest_status = str(result_summary.get("overall_status") or "")
    failure_type = str(triage.get("failure_type") or "NONE")
    flaky_warning = bool(gate.get("flaky_warning")) if gate else bool(trend.get("flaky_warning"))

    last_10_success_rate = _safe_float(gate.get("last_10_success_rate"), -1.0)
    if last_10_success_rate < 0.0:
        last_10_success_rate = _safe_float(trend.get("last_10_success_rate"), -1.0)
    if last_10_success_rate < 0.0:
        last_10_success_rate = _compute_last_10_success_rate(history)

    slowest_steps = trend.get("slowest_steps") if isinstance(trend.get("slowest_steps"), list) else []
    baseline_evaluation = gate.get("baseline_evaluation") if isinstance(gate.get("baseline_evaluation"), list) else []
    stderr_trend = trend.get("stderr_trend") if isinstance(trend.get("stderr_trend"), dict) else {}

    common_stderr_classifications = (
        stderr_trend.get("common_stderr_classifications")
        if isinstance(stderr_trend.get("common_stderr_classifications"), list)
        else []
    )
    noisy_steps = stderr_trend.get("noisy_steps") if isinstance(stderr_trend.get("noisy_steps"), list) else []
    real_error_steps = (
        stderr_trend.get("real_error_steps") if isinstance(stderr_trend.get("real_error_steps"), list) else []
    )

    stderr_trend_out = {
        "latest_stderr_classification": str(stderr_trend.get("latest_stderr_classification") or "NO_STDERR"),
        "last_10_stderr_noise_rate": _safe_float(stderr_trend.get("last_10_stderr_noise_rate"), 0.0),
        "last_10_real_stderr_rate": _safe_float(stderr_trend.get("last_10_real_stderr_rate"), 0.0),
        "common_stderr_classifications": common_stderr_classifications,
        "noisy_steps": noisy_steps,
        "real_error_steps": real_error_steps,
        "suggested_next_action": str(stderr_trend.get("suggested_next_action") or ""),
    }

    return {
        "latest_status": latest_status,
        "gate_status": gate_status,
        "failure_type": failure_type,
        "flaky_warning": flaky_warning,
        "last_10_success_rate": last_10_success_rate,
        "applied_preset": str(gate.get("applied_preset") or ""),
        "slowest_steps": slowest_steps,
        "baseline_evaluation": baseline_evaluation,
        "stderr_trend": stderr_trend_out,
    }


def get_scenario_router_ops_audit_latest(*, reports_dir: str | None = None) -> dict[str, Any]:
    base = _reports_dir(reports_dir)
    result, result_meta = _read_json_file(base / "scenario_router_audit_result.json")
    gate, gate_meta = _read_json_file(base / "scenario_router_audit_gate.json")
    history, history_meta = _read_jsonl_file(base / "scenario_router_audit_history.jsonl")
    trend_md, trend_meta = _read_text_file(base / "scenario_router_audit_trend.md")

    audit = _build_audit_summary(result=result, gate=gate, history=history)
    return {
        "audit": audit,
        "result": {
            "summary": (result.get("summary") if isinstance(result.get("summary"), dict) else {}),
            "triage": (result.get("triage") if isinstance(result.get("triage"), dict) else {}),
            "trend": (result.get("trend") if isinstance(result.get("trend"), dict) else {}),
        },
        "gate": {
            "status": str(gate.get("gate_status") or ""),
            "reasons": (gate.get("reasons") if isinstance(gate.get("reasons"), list) else []),
            "warnings": (gate.get("warnings") if isinstance(gate.get("warnings"), list) else []),
            "effective_config": (gate.get("effective_config") if isinstance(gate.get("effective_config"), dict) else {}),
        },
        "history": {
            "total_runs": len(history),
            "last_10_success_rate": _compute_last_10_success_rate(history),
            "latest": (history[-1] if history else {}),
        },
        "trend": {
            "markdown": trend_md,
        },
        "artifacts": {
            "result": result_meta,
            "gate": gate_meta,
            "history": history_meta,
            "trend": trend_meta,
        },
    }


def get_scenario_router_ops_audit_history(
    *,
    reports_dir: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    base = _reports_dir(reports_dir)
    rows, meta = _read_jsonl_file(base / "scenario_router_audit_history.jsonl")
    n = max(1, min(int(limit), 500))
    items = rows[-n:] if rows else []
    return {
        "items": items,
        "total": len(rows),
        "limit": n,
        "last_10_success_rate": _compute_last_10_success_rate(rows),
        "artifact": meta,
    }


def get_scenario_router_ops_incidents_latest(
    *,
    target: str = "win",
    limit: int = 20,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    n = _normalize_limit(limit, default=10, max_value=100)
    s = store or MLOpsStore()

    target_display, target_filter = _normalize_target_filter(target)

    rollout = {}
    if target_filter is not None:
        rollout = s.get_router_rollout(target=target_filter, create_if_missing=False) or {}

    open_alerts = s.list_router_alerts(target=target_filter, status="open", limit=500)
    latest_alerts = s.list_router_alerts(target=target_filter, status=None, limit=n)

    runbooks = s.list_router_runbooks(target=target_filter, limit=n)
    responses = s.list_incident_responses(target=target_filter, limit=n)
    actions = s.list_incident_actions(target=target_filter, limit=n)
    auto_recovery = s.list_auto_recovery_executions(limit=n)
    deliveries = s.list_router_notification_deliveries(target=target_filter, limit=n)

    latest_run = {}
    runs = s.list_router_rollout_runs(target=target_filter, limit=1)
    if runs:
        latest_run = runs[0]

    return {
        "rollout": {
            "status": str(rollout.get("status") or ""),
            "current_percent": int(rollout.get("current_percent") or 0),
            "previous_percent": int(rollout.get("previous_percent") or 0),
            "router_mode": str(rollout.get("router_mode") or ""),
            "last_decision": str(rollout.get("last_decision") or ""),
            "latest_run": latest_run,
            "target": target_display,
        },
        "alerts": {
            "open_count": len(open_alerts),
            "latest": latest_alerts,
        },
        "incidents": {
            "latest_runbooks": runbooks,
            "latest_responses": responses,
            "latest_actions": actions,
            "latest_auto_recovery_executions": auto_recovery,
        },
        "notifications": {
            "latest_deliveries": deliveries,
        },
    }


def _timeline_detail_href(entity_type: str, entity_id: str) -> str:
    et = str(entity_type or "").strip().lower()
    eid = str(entity_id or "").strip()
    if not eid:
        return ""
    mapping = {
        "alert": "alerts",
        "runbook": "runbooks",
        "incident_response": "responses",
        "incident_action": "actions",
        "auto_recovery_execution": "auto-recovery/executions",
        "notification_delivery": "notification-deliveries",
    }
    kind = mapping.get(et, "")
    if not kind:
        return ""
    return _detail_href(kind, eid)


def _is_dangerous_timeline_item(*, action_type: Any, status: Any) -> bool:
    at = str(action_type or "").strip().lower()
    st = str(status or "").strip().lower()
    if any(x in at for x in ["execute", "rollback", "stop", "resolve", "disable", "delete"]):
        return True
    return st in {"executed", "rollback", "resolved"}


def _build_timeline_item(
    *,
    timestamp: Any,
    entity_type: str,
    entity_id: Any,
    status: Any = "",
    severity: Any = "",
    action_type: Any = "",
    summary: Any = "",
) -> dict[str, Any]:
    eid = str(entity_id or "").strip()
    st = str(status or "")
    at = str(action_type or "")
    return {
        "timestamp": str(timestamp or ""),
        "entity_type": str(entity_type or ""),
        "entity_id": eid,
        "status": st,
        "severity": str(severity or ""),
        "action_type": at,
        "summary": str(summary or ""),
        "detail_url": _timeline_detail_href(str(entity_type or ""), eid),
        "is_dangerous": _is_dangerous_timeline_item(action_type=at, status=st),
        "read_only_note": "Read-only timeline. Use external manual approval flow for execution.",
    }


def get_scenario_router_incident_timeline(
    *,
    alert_id: str,
    target: str = "win",
    limit: int = 50,
    offset: int = 0,
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    aid = str(alert_id or "").strip()
    filters = _normalize_timeline_filters(
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    n = _normalize_limit(filters.get("limit"), default=50, max_value=200)
    fetch_n = max(200, min(500, max(0, _safe_int(filters.get("offset"), 0)) + n))
    target_display, target_filter = _normalize_target_filter(target)
    s = store or MLOpsStore()

    if not aid:
        pag = _filter_and_paginate_timeline_items([], filters=filters).get("pagination")
        return {
            "alert_id": "",
            "target": target_display,
            "not_found": True,
            "items": [],
            "summary": {
                "event_count": 0,
                "counts_by_entity": {},
                "latest_timestamp": "",
                "latest_entity_type": "",
                "latest_status": "",
            },
            "pagination": pag,
            "filters": {
                "entity_type": str(filters.get("entity_type") or "all"),
                "status": str(filters.get("status") or ""),
                "since": str(filters.get("since") or ""),
                "until": str(filters.get("until") or ""),
                "sort": str(filters.get("sort") or "desc"),
                "invalid": list(filters.get("invalid") or []),
            },
            "links": {
                "dashboard_html": "/api/mlops/research/scenario-router/ops/dashboard.html",
                "timeline": "/api/mlops/research/scenario-router/ops/timeline",
            },
        }

    alert = s.get_router_alert_by_id(alert_id=aid)
    if not alert:
        pag = _filter_and_paginate_timeline_items([], filters=filters).get("pagination")
        return {
            "alert_id": aid,
            "target": target_display,
            "not_found": True,
            "items": [],
            "summary": {
                "event_count": 0,
                "counts_by_entity": {},
                "latest_timestamp": "",
                "latest_entity_type": "",
                "latest_status": "",
            },
            "pagination": pag,
            "filters": {
                "entity_type": str(filters.get("entity_type") or "all"),
                "status": str(filters.get("status") or ""),
                "since": str(filters.get("since") or ""),
                "until": str(filters.get("until") or ""),
                "sort": str(filters.get("sort") or "desc"),
                "invalid": list(filters.get("invalid") or []),
            },
            "links": {
                "dashboard_html": "/api/mlops/research/scenario-router/ops/dashboard.html",
                "timeline": "/api/mlops/research/scenario-router/ops/timeline",
                "timeline_by_alert": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}",
                "timeline_by_alert_html": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html",
            },
        }

    alert_target = str(alert.get("target") or "")
    if target_filter is not None and alert_target and alert_target != target_filter:
        pag = _filter_and_paginate_timeline_items([], filters=filters).get("pagination")
        return {
            "alert_id": aid,
            "target": target_display,
            "not_found": True,
            "items": [],
            "summary": {
                "event_count": 0,
                "counts_by_entity": {},
                "latest_timestamp": "",
                "latest_entity_type": "",
                "latest_status": "",
            },
            "pagination": pag,
            "filters": {
                "entity_type": str(filters.get("entity_type") or "all"),
                "status": str(filters.get("status") or ""),
                "since": str(filters.get("since") or ""),
                "until": str(filters.get("until") or ""),
                "sort": str(filters.get("sort") or "desc"),
                "invalid": list(filters.get("invalid") or []),
            },
            "links": {
                "dashboard_html": "/api/mlops/research/scenario-router/ops/dashboard.html",
                "timeline": "/api/mlops/research/scenario-router/ops/timeline",
                "timeline_by_alert": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}",
                "timeline_by_alert_html": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html",
            },
        }

    runbooks = s.list_router_runbooks(alert_id=aid, limit=fetch_n)
    responses = s.list_incident_responses(alert_id=aid, limit=fetch_n)
    actions = s.list_incident_actions(alert_id=aid, limit=fetch_n)
    auto_recovery = s.list_auto_recovery_executions(alert_id=aid, limit=fetch_n)
    deliveries = s.list_router_notification_deliveries(alert_id=aid, target=target_filter, limit=fetch_n)

    timeline_items: list[dict[str, Any]] = []

    timeline_items.append(
        _build_timeline_item(
            timestamp=alert.get("created_at"),
            entity_type="alert",
            entity_id=alert.get("alert_id"),
            status=alert.get("status"),
            severity=alert.get("severity"),
            action_type=alert.get("action"),
            summary=alert.get("title") or alert.get("decision") or alert.get("alert_type"),
        )
    )

    for x in runbooks:
        if not isinstance(x, dict):
            continue
        timeline_items.append(
            _build_timeline_item(
                timestamp=x.get("created_at"),
                entity_type="runbook",
                entity_id=x.get("runbook_id"),
                status="GENERATED",
                severity=x.get("severity"),
                action_type="",
                summary=x.get("title") or x.get("summary") or x.get("alert_type"),
            )
        )

    for x in responses:
        if not isinstance(x, dict):
            continue
        summary_obj = x.get("summary") if isinstance(x.get("summary"), dict) else {}
        timeline_items.append(
            _build_timeline_item(
                timestamp=x.get("updated_at") or x.get("created_at"),
                entity_type="incident_response",
                entity_id=x.get("response_id"),
                status=x.get("status"),
                severity=x.get("severity"),
                action_type="",
                summary=summary_obj.get("summary") or summary_obj.get("title") or f"response:{str(x.get('status') or '')}",
            )
        )

    for x in actions:
        if not isinstance(x, dict):
            continue
        timeline_items.append(
            _build_timeline_item(
                timestamp=x.get("executed_at") or x.get("created_at"),
                entity_type="incident_action",
                entity_id=x.get("action_id"),
                status=x.get("status"),
                severity="",
                action_type=x.get("action_type"),
                summary=f"action:{str(x.get('action_type') or '')} dry_run={bool(x.get('dry_run'))}",
            )
        )

    for x in auto_recovery:
        if not isinstance(x, dict):
            continue
        timeline_items.append(
            _build_timeline_item(
                timestamp=x.get("executed_at") or x.get("created_at"),
                entity_type="auto_recovery_execution",
                entity_id=x.get("execution_id"),
                status=x.get("status"),
                severity="",
                action_type=x.get("action_type"),
                summary=(
                    f"auto_executed={bool(x.get('auto_executed'))}, "
                    f"manual_required={bool(x.get('manual_required'))}"
                ),
            )
        )

    for x in deliveries:
        if not isinstance(x, dict):
            continue
        timeline_items.append(
            _build_timeline_item(
                timestamp=x.get("sent_at") or x.get("created_at"),
                entity_type="notification_delivery",
                entity_id=x.get("delivery_id"),
                status=x.get("status"),
                severity="",
                action_type="dispatch",
                summary=f"channel={str(x.get('channel_id') or '')}, attempt={int(_safe_int(x.get('attempt_count'), 0))}",
            )
        )

    timeline_items = [x for x in timeline_items if isinstance(x, dict)]
    paged = _filter_and_paginate_timeline_items(timeline_items, filters=filters)
    filtered_all = paged.get("filtered") if isinstance(paged.get("filtered"), list) else []
    returned_items = paged.get("returned") if isinstance(paged.get("returned"), list) else []
    pagination = paged.get("pagination") if isinstance(paged.get("pagination"), dict) else {}

    counts_by_entity: dict[str, int] = {}
    for x in filtered_all:
        et = str(x.get("entity_type") or "")
        if not et:
            continue
        counts_by_entity[et] = int(counts_by_entity.get(et, 0)) + 1

    latest = filtered_all[0] if filtered_all else {}
    return {
        "alert_id": aid,
        "target": alert_target or target_display,
        "not_found": False,
        "alert": alert,
        "related": {
            "runbooks": runbooks,
            "responses": responses,
            "actions": actions,
            "auto_recovery_executions": auto_recovery,
            "notification_deliveries": deliveries,
        },
        "items": returned_items,
        "summary": {
            "event_count": len(filtered_all),
            "counts_by_entity": counts_by_entity,
            "latest_timestamp": str(latest.get("timestamp") or ""),
            "latest_entity_type": str(latest.get("entity_type") or ""),
            "latest_status": str(latest.get("status") or ""),
        },
        "pagination": pagination,
        "filters": {
            "entity_type": str(filters.get("entity_type") or "all"),
            "status": str(filters.get("status") or ""),
            "since": str(filters.get("since") or ""),
            "until": str(filters.get("until") or ""),
            "sort": str(filters.get("sort") or "desc"),
            "invalid": list(filters.get("invalid") or []),
        },
        "links": {
            "dashboard_html": "/api/mlops/research/scenario-router/ops/dashboard.html",
            "timeline": "/api/mlops/research/scenario-router/ops/timeline",
            "timeline_by_alert": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}",
            "timeline_by_alert_html": f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html",
            "alert_detail_html": _detail_href("alerts", aid),
        },
    }


def get_scenario_router_ops_timeline(
    *,
    target: str = "win",
    limit: int = 50,
    offset: int = 0,
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    filters = _normalize_timeline_filters(
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    n = _normalize_limit(filters.get("limit"), default=50, max_value=200)
    off = max(0, _safe_int(filters.get("offset"), 0))
    target_display, target_filter = _normalize_target_filter(target)
    s = store or MLOpsStore()
    alerts = s.list_router_alerts(target=target_filter, status=None, limit=200)

    out_items: list[dict[str, Any]] = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("alert_id") or "")
        if not aid:
            continue
        chain = get_scenario_router_incident_timeline(
            alert_id=aid,
            target=target_display,
            limit=200,
            offset=0,
            entity_type=str(filters.get("entity_type") or "all"),
            status=str(filters.get("status") or ""),
            since=str(filters.get("since") or ""),
            until=str(filters.get("until") or ""),
            sort=str(filters.get("sort") or "desc"),
            store=s,
        )
        summary = chain.get("summary") if isinstance(chain.get("summary"), dict) else {}
        counts = summary.get("counts_by_entity") if isinstance(summary.get("counts_by_entity"), dict) else {}
        event_count = int(summary.get("event_count") or 0)
        if event_count <= 0:
            continue
        latest_ts = str(summary.get("latest_timestamp") or "")
        out_items.append(
            {
                "alert_id": aid,
                "target": str(a.get("target") or target_display),
                "created_at": str(a.get("created_at") or ""),
                "severity": str(a.get("severity") or ""),
                "status": str(a.get("status") or ""),
                "title": str(a.get("title") or ""),
                "event_count": event_count,
                "latest_timestamp": latest_ts,
                "latest_entity_type": str(summary.get("latest_entity_type") or ""),
                "latest_status": str(summary.get("latest_status") or ""),
                "counts_by_entity": counts,
                "alert_detail_url": _detail_href("alerts", aid),
                "timeline_url": (
                    f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}"
                    + f"?entity_type={quote_plus(str(filters.get('entity_type') or 'all'))}"
                    + f"&status={quote_plus(str(filters.get('status') or ''))}"
                    + f"&since={quote_plus(str(filters.get('since') or ''))}"
                    + f"&until={quote_plus(str(filters.get('until') or ''))}"
                    + f"&sort={quote_plus(str(filters.get('sort') or 'desc'))}"
                    + "&limit=50&offset=0"
                ),
                "timeline_html_url": (
                    f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html"
                    + f"?entity_type={quote_plus(str(filters.get('entity_type') or 'all'))}"
                    + f"&status={quote_plus(str(filters.get('status') or ''))}"
                    + f"&since={quote_plus(str(filters.get('since') or ''))}"
                    + f"&until={quote_plus(str(filters.get('until') or ''))}"
                    + f"&sort={quote_plus(str(filters.get('sort') or 'desc'))}"
                    + "&limit=50&offset=0"
                ),
            }
        )

    out_items.sort(
        key=lambda x: (str(x.get("latest_timestamp") or x.get("created_at") or ""), str(x.get("alert_id") or "")),
        reverse=(str(filters.get("sort") or "desc") == "desc"),
    )
    paged_items = out_items[off : off + n]
    has_more = (off + n) < len(out_items)

    return {
        "target": target_display,
        "limit": n,
        "items": paged_items,
        "total": len(out_items),
        "pagination": {
            "limit": n,
            "offset": off,
            "returned": len(paged_items),
            "has_more": has_more,
            "next_offset": (off + n) if has_more else off,
        },
        "filters": {
            "entity_type": str(filters.get("entity_type") or "all"),
            "status": str(filters.get("status") or ""),
            "since": str(filters.get("since") or ""),
            "until": str(filters.get("until") or ""),
            "sort": str(filters.get("sort") or "desc"),
            "invalid": list(filters.get("invalid") or []),
        },
    }


def build_scenario_router_ops_timeline_export(
    *,
    format: str,
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> tuple[str, str, str] | dict[str, Any]:
    timeline = get_scenario_router_ops_timeline_items(
        target=target,
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
        store=store,
    )
    fmt = str(format or "").strip().lower()
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if fmt == "json":
        return timeline
    if fmt == "markdown":
        return (build_timeline_export_markdown(timeline=timeline, generated_at=generated_at), "text/markdown; charset=utf-8", "timeline.md")
    if fmt == "csv":
        return (build_timeline_export_csv(timeline=timeline), "text/csv; charset=utf-8", "timeline.csv")
    raise ValueError("invalid format: choose markdown/csv/json")


def build_scenario_router_incident_timeline_export(
    *,
    alert_id: str,
    format: str,
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> tuple[str, str, str] | dict[str, Any]:
    timeline = get_scenario_router_incident_timeline(
        alert_id=alert_id,
        target=target,
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
        store=store,
    )

    summary = timeline.get("summary") if isinstance(timeline.get("summary"), dict) else {}
    export_payload = {
        "summary": {
            "alert_id": str(timeline.get("alert_id") or ""),
            "target": str(timeline.get("target") or ""),
            "not_found": bool(timeline.get("not_found")),
            "total_events": int(summary.get("event_count") or 0),
            "latest_timestamp": str(summary.get("latest_timestamp") or ""),
            "latest_entity_type": str(summary.get("latest_entity_type") or ""),
            "latest_status": str(summary.get("latest_status") or ""),
            "counts_by_entity": summary.get("counts_by_entity") if isinstance(summary.get("counts_by_entity"), dict) else {},
        },
        "filters": timeline.get("filters") if isinstance(timeline.get("filters"), dict) else {},
        "pagination": timeline.get("pagination") if isinstance(timeline.get("pagination"), dict) else {},
        "items": timeline.get("items") if isinstance(timeline.get("items"), list) else [],
    }

    fmt = str(format or "").strip().lower()
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if fmt == "json":
        return export_payload
    if fmt == "markdown":
        return (
            build_timeline_export_markdown(timeline=export_payload, generated_at=generated_at),
            "text/markdown; charset=utf-8",
            f"timeline_{str(alert_id or 'unknown')}.md",
        )
    if fmt == "csv":
        return (
            build_timeline_export_csv(timeline=export_payload),
            "text/csv; charset=utf-8",
            f"timeline_{str(alert_id or 'unknown')}.csv",
        )
    raise ValueError("invalid format: choose markdown/csv/json")


def _timeline_event_label(entity_type: Any) -> str:
    et = str(entity_type or "")
    mapping = {
        "alert": "Alert",
        "runbook": "Runbook",
        "incident_response": "Incident Response",
        "incident_action": "Incident Action",
        "auto_recovery_execution": "Auto Recovery Execution",
        "notification_delivery": "Notification Delivery",
    }
    return str(mapping.get(et, et or "Unknown"))


def _normalize_report_style(style: str) -> str:
    s = str(style or "default").strip().lower()
    if s in {"", "default"}:
        return "default"
    if s == "notion":
        return "notion"
    raise ValueError("invalid style: choose default/notion")


def build_incident_report_markdown(*, timeline: dict[str, Any], generated_at: str) -> str:
    summary = timeline.get("summary") if isinstance(timeline.get("summary"), dict) else {}
    filters = timeline.get("filters") if isinstance(timeline.get("filters"), dict) else {}
    pagination = timeline.get("pagination") if isinstance(timeline.get("pagination"), dict) else {}
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []

    alert_id = str(summary.get("alert_id") or "")
    total_events = int(_safe_int(summary.get("total_events"), len(items)))
    latest_event_at = str(summary.get("latest_timestamp") or "")

    first_event_at = ""
    severity = ""
    latest_status = str(summary.get("latest_status") or "")
    if items:
        sorted_asc = sorted(
            [x for x in items if isinstance(x, dict)],
            key=lambda x: str(x.get("timestamp") or ""),
        )
        first = sorted_asc[0] if sorted_asc else {}
        first_event_at = str(first.get("timestamp") or "")
        if not latest_event_at:
            latest_event_at = str((sorted_asc[-1] if sorted_asc else {}).get("timestamp") or "")
        for x in sorted_asc:
            sev = str(x.get("severity") or "")
            if sev:
                severity = sev
                break
        if not latest_status:
            latest_status = str((sorted_asc[-1] if sorted_asc else {}).get("status") or "")

    actions_taken = [
        x
        for x in items
        if isinstance(x, dict)
        and str(x.get("entity_type") or "") in {"incident_action", "auto_recovery_execution", "notification_delivery"}
    ]

    manual_required_items: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        st = str(x.get("status") or "").strip().lower()
        summary_txt = str(x.get("summary") or "")
        dangerous = bool(x.get("is_dangerous"))
        manual_required = ("manual_required=true" in summary_txt.lower())
        if dangerous or manual_required:
            manual_required_items.append(x)
        if st in {"open", "failed", "skipped", "blocked"} or manual_required:
            open_items.append(x)

    suggested_next_action = "Review latest timeline and detail pages; proceed only via manual approval flow."
    if open_items:
        suggested_next_action = "Open items detected (FAILED/SKIPPED/BLOCKED/manual_required). Prioritize detail page review and manual approval checks."
    elif total_events == 0:
        suggested_next_action = "No events matched current filters. Expand filters or time range and re-check timeline."

    detail_urls: list[str] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        u = str(x.get("detail_url") or "")
        if u and u not in detail_urls:
            detail_urls.append(u)
    detail_urls = detail_urls[:8]

    flow_labels = [
        _timeline_event_label(x.get("entity_type"))
        for x in items
        if isinstance(x, dict)
    ]
    uniq_flow: list[str] = []
    for f in flow_labels:
        if f and f not in uniq_flow:
            uniq_flow.append(f)
    flow_text = " -> ".join(uniq_flow[:6]) if uniq_flow else "no events"

    lines: list[str] = []
    lines.append("# Scenario Router Incident Report")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append(f"- alert_id: {_markdown_cell(alert_id)}")
    lines.append(f"- severity: {_markdown_cell(severity)}")
    lines.append(f"- latest_status: {_markdown_cell(latest_status)}")
    lines.append(f"- total_events: {_markdown_cell(total_events)}")
    lines.append(f"- first_event_at: {_markdown_cell(first_event_at)}")
    lines.append(f"- latest_event_at: {_markdown_cell(latest_event_at)}")
    lines.append(f"- suggested_next_action: {_markdown_cell(suggested_next_action)}")
    lines.append("")

    lines.append("## 2. What Happened")
    if total_events > 0:
        lines.append(
            _markdown_cell(
                f"Timeline contains {total_events} events from {first_event_at or 'N/A'} to {latest_event_at or 'N/A'}. "
                f"Observed flow: {flow_text}."
            )
        )
    else:
        lines.append("No timeline events matched the current filters.")
    lines.append("")

    lines.append("## 3. Timeline")
    lines.append("| timestamp | event | status | summary |")
    lines.append("|---|---|---|---|")
    if items:
        for x in items:
            if not isinstance(x, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(x.get("timestamp") or ""),
                        _markdown_cell(_timeline_event_label(x.get("entity_type"))),
                        _markdown_cell(x.get("status") or ""),
                        _markdown_cell(x.get("summary") or ""),
                    ]
                )
                + " |"
            )
    else:
        lines.append("|  |  |  | no events |")
    lines.append("")

    lines.append("## 4. Actions Taken")
    if actions_taken:
        for x in actions_taken:
            lines.append(
                "- "
                + _markdown_cell(
                    f"{str(x.get('timestamp') or '')} | {_timeline_event_label(x.get('entity_type'))} | "
                    f"status={str(x.get('status') or '')} | {str(x.get('summary') or '')}"
                )
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## 5. Manual Approval Required")
    if manual_required_items:
        for x in manual_required_items:
            lines.append(
                "- "
                + _markdown_cell(
                    f"{str(x.get('timestamp') or '')} | {_timeline_event_label(x.get('entity_type'))} | "
                    f"status={str(x.get('status') or '')} | {str(x.get('summary') or '')}"
                )
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## 6. Open Items")
    if open_items:
        for x in open_items:
            lines.append(
                "- "
                + _markdown_cell(
                    f"{str(x.get('timestamp') or '')} | {_timeline_event_label(x.get('entity_type'))} | "
                    f"status={str(x.get('status') or '')} | {str(x.get('summary') or '')}"
                )
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## 7. Recommended Next Actions")
    lines.append("- Review related detail pages for open or risky items.")
    if detail_urls:
        for u in detail_urls:
            lines.append(f"- detail: {_markdown_cell(u)}")
    lines.append("- Validate with read-only preview APIs before any manual approval path.")
    lines.append(f"- generated_at: {_markdown_cell(generated_at)}")
    lines.append(f"- filters: {_markdown_cell(json.dumps(filters, ensure_ascii=False, default=str))}")
    lines.append(f"- pagination: {_markdown_cell(json.dumps(pagination, ensure_ascii=False, default=str))}")
    lines.append("")

    lines.append("## 8. Read-only Notice")
    lines.append("This report is generated from read-only timeline data.")
    lines.append("Dangerous operations are intentionally not executed or included as executable UI.")
    lines.append("")
    return "\n".join(lines)


def build_incident_report_markdown_notion(*, timeline: dict[str, Any], generated_at: str) -> str:
    summary = timeline.get("summary") if isinstance(timeline.get("summary"), dict) else {}
    filters = timeline.get("filters") if isinstance(timeline.get("filters"), dict) else {}
    pagination = timeline.get("pagination") if isinstance(timeline.get("pagination"), dict) else {}
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []

    alert_id = str(summary.get("alert_id") or "")
    total_events = int(_safe_int(summary.get("total_events"), len(items)))
    latest_event_at = str(summary.get("latest_timestamp") or "")
    latest_status = str(summary.get("latest_status") or "")

    first_event_at = ""
    severity = ""
    sorted_asc: list[dict[str, Any]] = []
    if items:
        sorted_asc = sorted(
            [x for x in items if isinstance(x, dict)],
            key=lambda x: str(x.get("timestamp") or ""),
        )
        first = sorted_asc[0] if sorted_asc else {}
        first_event_at = str(first.get("timestamp") or "")
        if not latest_event_at:
            latest_event_at = str((sorted_asc[-1] if sorted_asc else {}).get("timestamp") or "")
        for x in sorted_asc:
            sev = str(x.get("severity") or "")
            if sev:
                severity = sev
                break
        if not latest_status:
            latest_status = str((sorted_asc[-1] if sorted_asc else {}).get("status") or "")

    manual_required_items: list[dict[str, Any]] = []
    open_items: list[dict[str, Any]] = []
    has_failed_or_blocked = False
    has_unresolved_alert = False
    has_manual_required_action = False
    has_auto_recovery = False
    has_notification_delivery = False

    for x in items:
        if not isinstance(x, dict):
            continue
        et = str(x.get("entity_type") or "")
        st = str(x.get("status") or "").strip().lower()
        summary_txt = str(x.get("summary") or "")
        dangerous = bool(x.get("is_dangerous"))
        manual_required = ("manual_required=true" in summary_txt.lower())

        if et == "auto_recovery_execution":
            has_auto_recovery = True
            if manual_required:
                has_manual_required_action = True
        if et == "notification_delivery":
            has_notification_delivery = True
        if et == "alert" and st in {"open", "firing", "pending", "failed"}:
            has_unresolved_alert = True
        if et in {"incident_action", "auto_recovery_execution"} and st in {"failed", "skipped", "blocked"}:
            has_failed_or_blocked = True

        if dangerous or manual_required:
            manual_required_items.append(x)
        if st in {"open", "failed", "skipped", "blocked"} or manual_required:
            open_items.append(x)

    suggested_next_action = "Check detail pages and preview APIs before any manual approval process outside dashboard."
    if open_items:
        suggested_next_action = "Open items detected. Prioritize FAILED/SKIPPED/BLOCKED and manual-required items."
    elif total_events == 0:
        suggested_next_action = "No events matched current filters. Widen the time range and entity filters."

    flow_labels = [_timeline_event_label(x.get("entity_type")) for x in sorted_asc]
    uniq_flow: list[str] = []
    for f in flow_labels:
        if f and f not in uniq_flow:
            uniq_flow.append(f)
    flow_text = " -> ".join(uniq_flow[:6]) if uniq_flow else "no events"

    lines: list[str] = []
    lines.append("# Scenario Router Incident Report")
    lines.append("")
    lines.append("## 🧭 Executive Summary")
    lines.append(f"- Alert ID: {_markdown_cell(alert_id)}")
    lines.append(f"- Severity: {_markdown_cell(severity)}")
    lines.append(f"- Latest Status: {_markdown_cell(latest_status)}")
    lines.append(f"- Total Events: {_markdown_cell(total_events)}")
    lines.append(f"- First Event: {_markdown_cell(first_event_at)}")
    lines.append(f"- Latest Event: {_markdown_cell(latest_event_at)}")
    lines.append(f"- Suggested Next Action: {_markdown_cell(suggested_next_action)}")
    lines.append("")

    lines.append("> [!NOTE]")
    lines.append("> Read-only investigation report for sharing in Notion and incident channels.")
    lines.append("> Dangerous actions are intentionally excluded from executable UI.")
    lines.append("")

    lines.append("## 🚨 What Happened")
    if total_events > 0:
        lines.append(
            _markdown_cell(
                f"This incident contains {total_events} timeline events from {first_event_at or 'N/A'} to {latest_event_at or 'N/A'}. "
                f"Observed flow: {flow_text}."
            )
        )
    else:
        lines.append("No timeline events matched current filters.")
    lines.append("")

    lines.append("## 🕒 Timeline")
    lines.append("| Time | Event | Status | Summary |")
    lines.append("|---|---|---|---|")
    if items:
        for x in items:
            if not isinstance(x, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(x.get("timestamp") or ""),
                        _markdown_cell(_timeline_event_label(x.get("entity_type"))),
                        _markdown_cell(x.get("status") or ""),
                        _markdown_cell(x.get("summary") or ""),
                    ]
                )
                + " |"
            )
    else:
        lines.append("|  |  |  | no events |")
    lines.append("")

    lines.append("## ✅ Actions Taken")
    lines.append(f"- [{'x' if has_auto_recovery else ' '}] Auto Recovery evaluated")
    lines.append(f"- [{'x' if has_notification_delivery else ' '}] Notification delivered")
    lines.append(f"- [{' ' if manual_required_items else 'x'}] Manual approval pending")
    lines.append("")

    lines.append("## ⚠️ Manual Approval Required")
    lines.append(f"- [{' ' if manual_required_items else 'x'}] Review dangerous action")
    lines.append("- [ ] Confirm whether rollback/stop is needed outside dashboard")
    if manual_required_items:
        lines.append("")
        for x in manual_required_items[:10]:
            lines.append(
                "- [ ] "
                + _markdown_cell(
                    f"{str(x.get('timestamp') or '')} | {_timeline_event_label(x.get('entity_type'))} | "
                    f"status={str(x.get('status') or '')} | {str(x.get('summary') or '')}"
                )
            )
    lines.append("")

    lines.append("## 🧩 Open Items")
    lines.append(f"- [{'x' if has_failed_or_blocked else ' '}] FAILED / SKIPPED / BLOCKED action")
    lines.append(f"- [{'x' if has_unresolved_alert else ' '}] unresolved alert")
    lines.append(f"- [{'x' if has_manual_required_action else ' '}] manual_required action")
    for x in open_items[:10]:
        lines.append(
            "- [ ] "
            + _markdown_cell(
                f"{str(x.get('timestamp') or '')} | {_timeline_event_label(x.get('entity_type'))} | "
                f"status={str(x.get('status') or '')} | {str(x.get('summary') or '')}"
            )
        )
    if not open_items:
        lines.append("- [x] no open items")
    lines.append("")

    lines.append("## 🔎 Recommended Next Checks")
    lines.append("- [ ] Check detail page")
    lines.append("- [ ] Run preview API")
    lines.append("- [ ] Review timeline export")
    lines.append(f"- filters: {_markdown_cell(json.dumps(filters, ensure_ascii=False, default=str))}")
    lines.append(f"- pagination: {_markdown_cell(json.dumps(pagination, ensure_ascii=False, default=str))}")
    lines.append(f"- generated_at: {_markdown_cell(generated_at)}")
    lines.append("")

    lines.append("## 🛡️ Read-only Notice")
    lines.append("This report is generated from read-only timeline data.")
    lines.append("Dangerous operations are intentionally not executed or included as executable UI.")
    lines.append("")
    return "\n".join(lines)


def build_scenario_router_ops_timeline_report(
    *,
    format: str,
    style: str = "default",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> tuple[str, str, str]:
    fmt = str(format or "").strip().lower()
    if fmt != "markdown":
        raise ValueError("invalid format: choose markdown")
    report_style = _normalize_report_style(style)
    timeline = get_scenario_router_ops_timeline_items(
        target=target,
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
        store=store,
    )
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    text = build_incident_report_markdown(timeline=timeline, generated_at=generated_at)
    if report_style == "notion":
        text = build_incident_report_markdown_notion(timeline=timeline, generated_at=generated_at)
    return (
        text,
        "text/markdown; charset=utf-8",
        f"incident_report_{report_style}.md",
    )


def build_scenario_router_incident_timeline_report(
    *,
    alert_id: str,
    format: str,
    style: str = "default",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
    offset: int = 0,
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> tuple[str, str, str]:
    fmt = str(format or "").strip().lower()
    if fmt != "markdown":
        raise ValueError("invalid format: choose markdown")
    report_style = _normalize_report_style(style)
    timeline = build_scenario_router_incident_timeline_export(
        alert_id=alert_id,
        format="json",
        target=target,
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        sort=sort,
        store=store,
    )
    payload = timeline if isinstance(timeline, dict) else {"summary": {}, "filters": {}, "pagination": {}, "items": []}
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    text = build_incident_report_markdown(timeline=payload, generated_at=generated_at)
    if report_style == "notion":
        text = build_incident_report_markdown_notion(timeline=payload, generated_at=generated_at)
    return (
        text,
        "text/markdown; charset=utf-8",
        f"incident_report_{str(alert_id or 'unknown')}_{report_style}.md",
    )


def get_scenario_router_ops_dashboard(
    *,
    target: str = "win",
    reports_dir: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    n = _normalize_limit(limit, default=10, max_value=100)
    target_display, _ = _normalize_target_filter(target)

    audit_latest = get_scenario_router_ops_audit_latest(reports_dir=reports_dir)
    incidents_latest = get_scenario_router_ops_incidents_latest(target=target_display, limit=n)
    timeline_summary = get_scenario_router_ops_timeline(target=target_display, limit=min(n, 20))

    audit = audit_latest.get("audit") if isinstance(audit_latest.get("audit"), dict) else {}
    rollout = incidents_latest.get("rollout") if isinstance(incidents_latest.get("rollout"), dict) else {}
    alerts = incidents_latest.get("alerts") if isinstance(incidents_latest.get("alerts"), dict) else {}

    suggested_next_action = "No immediate operational action required."
    if str(audit.get("gate_status") or "") == "FAIL":
        suggested_next_action = "Quality Gate is FAIL. Check triage and rerun audit in sandbox mode."
    elif str(audit.get("gate_status") or "") == "WARN":
        suggested_next_action = "Quality Gate is WARN. Review baseline and flaky signals before tightening policy."
    elif int(alerts.get("open_count") or 0) > 0:
        suggested_next_action = "Open alerts exist. Review latest incident response and runbook recommendations."
    elif str(rollout.get("status") or "") in {"STOPPED", "ROLLBACK"}:
        suggested_next_action = "Rollout indicates risk state. Verify canary metrics before re-enabling active routing."

    return {
        "audit": audit,
        "rollout": rollout,
        "alerts": alerts,
        "incidents": incidents_latest.get("incidents") if isinstance(incidents_latest.get("incidents"), dict) else {},
        "notifications": incidents_latest.get("notifications") if isinstance(incidents_latest.get("notifications"), dict) else {},
        "incident_timeline": timeline_summary,
        "artifacts": audit_latest.get("artifacts") if isinstance(audit_latest.get("artifacts"), dict) else {},
        "applied_filters": {
            "target": target_display,
            "limit": n,
        },
        "suggested_next_action": suggested_next_action,
    }


def _esc(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _status_class(status: str) -> str:
    s = str(status or "").upper()
    if s == "PASS":
        return "status-pass"
    if s == "WARN":
        return "status-warn"
    if s == "FAIL":
        return "status-fail"
    return "status-info"


def _status_badge(status: str) -> str:
    txt = str(status or "UNKNOWN")
    cls = _status_class(txt)
    return f"<span class='status {cls}'>{_esc(txt)}</span>"


def _fmt_pct(value: Any) -> str:
    v = _safe_float(value, 0.0)
    return f"{(v * 100.0):.2f}%"


def _table_or_empty(headers: list[str], rows: list[list[Any]], empty_label: str = "none") -> str:
    if not rows:
        return f"<div class='muted'>{_esc(empty_label)}</div>"
    head_html = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body_rows = []
    for r in rows:
        cells = "".join(f"<td>{_esc(c)}</td>" for c in r)
        body_rows.append(f"<tr>{cells}</tr>")
    body_html = "".join(body_rows)
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _table_or_empty_html(headers: list[str], rows_html: list[list[str]], empty_label: str = "none") -> str:
    if not rows_html:
        return f"<div class='muted'>{_esc(empty_label)}</div>"
    head_html = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body_rows = []
    for r in rows_html:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body_rows.append(f"<tr>{cells}</tr>")
    body_html = "".join(body_rows)
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _render_top_name_count(items: Any, *, name_key: str, count_key: str, top_n: int = 8) -> str:
    rows_html: list[list[str]] = []
    arr = items if isinstance(items, list) else []
    for x in arr[: max(1, int(top_n))]:
        if not isinstance(x, dict):
            continue
        nm = _esc(str(x.get(name_key) or ""))
        ct = _esc(int(_safe_int(x.get(count_key), 0)))
        if not nm:
            continue
        rows_html.append([nm, ct])
    return _table_or_empty_html([name_key, count_key], rows_html, empty_label="none")


def _detail_href(kind: str, item_id: str) -> str:
    k = str(kind or "").strip().strip("/")
    iid = quote_plus(str(item_id or "").strip())
    return f"/api/mlops/research/scenario-router/ops/{k}/{iid}.html"


def _build_readonly_curl_snippets(*, target: str, limit: int, refresh: int, show_raw_links: bool) -> list[dict[str, str]]:
    t = quote_plus(str(target or "win"))
    l = max(1, min(int(limit), 100))
    r = max(0, min(int(refresh), 3600))
    s = str(bool(show_raw_links)).lower()
    base = "${API_BASE_URL:-http://127.0.0.1:8000}"

    return [
        {
            "name": "GET Ops Dashboard",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/dashboard?target={t}&limit={l}&refresh={r}&show_raw_links={s}\"",
        },
        {
            "name": "GET Ops Audit Latest",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/audit/latest\"",
        },
        {
            "name": "GET Ops Audit History",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/audit/history?limit={l}\"",
        },
        {
            "name": "GET Ops Incidents Latest",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/incidents/latest?target={t}&limit={l}\"",
        },
        {
            "name": "GET Ops Timeline",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/timeline?target={t}&limit={l}\"",
        },
        {
            "name": "GET Ops Timeline by Alert",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/ops/timeline/<ALERT_ID>?target={t}&limit=200\"",
        },
        {
            "name": "GET Scenario Router Rollout Status",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/rollout/status?target={t}\"",
        },
        {
            "name": "GET Scenario Router Alerts",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/alerts?target={t}&status=open&limit={l}\"",
        },
        {
            "name": "GET Notification Deliveries",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/notifications/deliveries?target={t}&limit={l}\"",
        },
        {
            "name": "GET Auto Recovery Executions",
            "curl": f"curl -sS \"{base}/api/mlops/research/scenario-router/auto-recovery/executions?limit={l}\"",
        },
    ]


def _build_action_preview_post_snippets(*, target: str, limit: int) -> list[dict[str, str]]:
    t = quote_plus(str(target or "win"))
    l = max(1, min(int(limit), 100))
    base = "${API_BASE_URL:-http://127.0.0.1:8000}"
    token = '${E2E_BEARER_TOKEN:-<TOKEN>}'

    return [
        {
            "name": "POST Incident Action Preview",
            "curl": (
                "curl -sS -X POST "
                + f"\"{base}/api/mlops/research/scenario-router/incidents/actions/preview\" "
                + "-H \"Authorization: Bearer " + token + "\" "
                + "-H \"Content-Type: application/json\" "
                + "-d '{\"alert_id\":\"<ALERT_ID>\",\"runbook_id\":\"\"}'"
            ),
        },
        {
            "name": "POST Incident Response Prepare",
            "curl": (
                "curl -sS -X POST "
                + f"\"{base}/api/mlops/research/scenario-router/incidents/response/prepare\" "
                + "-H \"Authorization: Bearer " + token + "\" "
                + "-H \"Content-Type: application/json\" "
                + "-d '{\"alert_id\":\"<ALERT_ID>\",\"save_response\":false,\"include_runbook_summary\":true,\"notification_channel_type\":\"slack\",\"include_action_preview\":true}'"
            ),
        },
        {
            "name": "POST Auto Recovery Evaluate",
            "curl": (
                "curl -sS -X POST "
                + f"\"{base}/api/mlops/research/scenario-router/auto-recovery/evaluate\" "
                + "-H \"Authorization: Bearer " + token + "\" "
                + "-H \"Content-Type: application/json\" "
                + "-d '{\"response_id\":\"\",\"alert_id\":\"<ALERT_ID>\",\"include_action_preview\":true,\"include_runbook_summary\":true,\"notification_channel_type\":\"slack\"}'"
            ),
        },
        {
            "name": "POST Runbook Generate",
            "curl": (
                "curl -sS -X POST "
                + f"\"{base}/api/mlops/research/scenario-router/runbooks/generate\" "
                + "-H \"Authorization: Bearer " + token + "\" "
                + "-H \"Content-Type: application/json\" "
                + "-d '{\"alert_id\":\"<ALERT_ID>\",\"include_notification_summary\":true,\"save_runbook\":false}'"
            ),
        },
        {
            "name": "POST Notification Test",
            "curl": (
                "curl -sS -X POST "
                + f"\"{base}/api/mlops/research/scenario-router/notifications/test\" "
                + "-H \"Authorization: Bearer " + token + "\" "
                + "-H \"Content-Type: application/json\" "
                + "-d '{\"channel_type\":\"webhook\",\"name\":\"ops-preview\",\"config\":{\"url\":\"https://example.invalid/hook\"},\"payload\":{\"target\":\""
                + f"{t}"
                + "\",\"limit\":"
                + f"{l}"
                + "},\"alert_id\":\"<ALERT_ID>\",\"include_runbook_summary\":true,\"apply_send\":false}'"
            ),
        },
    ]


def _render_snippet_block(index: int, name: str, curl_cmd: str) -> str:
    sid = f"snippet_{index}"
    bid = f"btn_{index}"
    return (
        "<div style='margin:8px 0 12px;'>"
        + f"<div class='snippet-head'><div class='muted' style='margin-bottom:4px;'>{_esc(name)}</div>"
        + f"<button type='button' class='copy-btn' data-target-id='{_esc(sid)}' id='{_esc(bid)}'>Copy</button></div>"
        + f"<pre><code id='{_esc(sid)}'>{_esc(curl_cmd)}</code></pre>"
        + "</div>"
    )


def _render_copy_script() -> str:
    return """
    <script>
        (function () {
            function flashCopied(btn, ok) {
                var original = btn.getAttribute('data-label') || 'Copy';
                btn.setAttribute('data-label', original);
                btn.textContent = ok ? 'Copied' : 'Copy failed';
                if (ok) btn.classList.add('copied');
                setTimeout(function () {
                    btn.textContent = original;
                    btn.classList.remove('copied');
                }, 1400);
            }

            function fallbackCopy(text) {
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.setAttribute('readonly', 'readonly');
                ta.style.position = 'fixed';
                ta.style.left = '-9999px';
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                var ok = false;
                try {
                    ok = document.execCommand('copy');
                } catch (e) {
                    ok = false;
                }
                document.body.removeChild(ta);
                return ok;
            }

            function copyText(text, onDone) {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(
                        function () { onDone(true); },
                        function () { onDone(fallbackCopy(text)); }
                    );
                    return;
                }
                onDone(fallbackCopy(text));
            }

            var buttons = document.querySelectorAll('.copy-btn[data-target-id]');
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].addEventListener('click', function (ev) {
                    var btn = ev.currentTarget;
                    var id = btn.getAttribute('data-target-id') || '';
                    var node = id ? document.getElementById(id) : null;
                    var text = node ? (node.textContent || '') : '';
                    if (!text) {
                        flashCopied(btn, false);
                        return;
                    }
                    copyText(text, function (ok) { flashCopied(btn, ok); });
                });
            }
        })();
    </script>
"""


def _is_safe_preview_snippet(snippet: dict[str, str]) -> bool:
    name = str(snippet.get("name") or "").lower()
    curl_cmd = str(snippet.get("curl") or "").lower()
    allowed = any(x in name for x in ["preview", "prepare", "evaluate", "generate", "test"])
    blocked = any(x in name or x in curl_cmd for x in ["execute", "rollback", "stop", "resolve", "disable", "delete"])
    return bool(allowed and not blocked)


def _build_manual_approval_guide(entity_type: str, item: dict[str, Any]) -> dict[str, list[str]]:
    et = str(entity_type or "").strip().lower()
    status = str(item.get("status") or "").strip().upper()
    severity = str(item.get("severity") or "").strip().upper()
    action_type = str(item.get("action_type") or "").strip().upper()
    failure_type = str(item.get("failure_type") or item.get("alert_type") or "").strip().upper()
    dry_run = bool(item.get("dry_run"))
    manual_required = bool(item.get("manual_required"))
    auto_executed = bool(item.get("auto_executed"))

    checks: list[str] = [
        "Review escaped Raw JSON and confirm IDs/target before any manual operation.",
        "Use preview/evaluate/generate/test APIs first and keep apply_updates=false on external execution path.",
    ]
    safe_previews: list[str] = []
    manual_actions: list[str] = [
        "All state-changing operations require manual approval and must be executed outside this dashboard.",
        "Dangerous operations intentionally not rendered from this UI.",
    ]
    followups: list[str] = []

    if et == "alert":
        safe_previews = [
            "Runbook generate preview",
            "Incident response prepare preview",
            "Auto recovery evaluate preview",
        ]
        checks.append(f"Inspect alert severity/status first: severity={severity or 'UNKNOWN'}, status={status or 'UNKNOWN'}.")
        if severity in {"HIGH", "CRITICAL"}:
            manual_actions.append("High-severity alert: obtain explicit approver sign-off before any state change.")
        if failure_type:
            checks.append(f"Validate failure type context before approval: {failure_type}.")
        followups = [
            "Open related runbooks for this alert.",
            "Open incident responses linked to this alert.",
            "Review auto recovery executions for the same alert.",
        ]
    elif et == "runbook":
        safe_previews = [
            "Incident action preview",
            "Incident response prepare preview",
        ]
        checks.append("Confirm runbook assumptions against latest alert status and metrics.")
        followups = [
            "Open the source alert detail.",
            "Open incident responses tied to this runbook/alert.",
        ]
    elif et == "response":
        safe_previews = [
            "Incident action preview",
            "Auto recovery evaluate preview",
        ]
        checks.append(f"Check response status freshness: {status or 'UNKNOWN'}.")
        followups = [
            "Open related alert and runbook details.",
            "Review linked auto recovery executions before approval.",
        ]
    elif et == "action":
        safe_previews = [
            "Incident action preview",
            "Auto recovery evaluate preview",
        ]
        checks.append(
            "Confirm whether action is DRY_RUN / EXECUTED / FAILED / SKIPPED before deciding on any manual execution path."
        )
        checks.append(f"Current action context: status={status or 'UNKNOWN'}, action_type={action_type or 'UNKNOWN'}, dry_run={dry_run}.")
        if (not dry_run) or status in {"FAILED", "SKIPPED"}:
            manual_actions.append("This action needs explicit manual approval review due to non-dry-run or non-success status.")
        followups = [
            "Open related incident response and alert details.",
            "Re-run preview API to validate latest policy impact.",
        ]
    elif et == "auto_recovery_execution":
        safe_previews = [
            "Auto recovery evaluate preview",
            "Incident action preview",
        ]
        checks.append(
            "Check which actions were auto_executed / manual_required / skipped and validate against current incident response."
        )
        checks.append(
            f"Execution flags: auto_executed={auto_executed}, manual_required={manual_required}, status={status or 'UNKNOWN'}."
        )
        if manual_required:
            manual_actions.append("manual_required=true: proceed only through external manual approval workflow.")
        followups = [
            "Open related incident response detail.",
            "Open source alert detail for final approval context.",
        ]

    return {
        "recommended_next_checks": checks,
        "safe_preview_commands": safe_previews,
        "manual_approval_required_actions": manual_actions,
        "suggested_follow_up_checks": followups,
    }


def _render_text_list(items: list[str], *, empty_label: str = "none") -> str:
    if not items:
        return f"<div class='muted'>{_esc(empty_label)}</div>"
    lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
    return f"<ul>{lis}</ul>"


def _render_related_links(links: list[dict[str, str]]) -> str:
    rows: list[list[str]] = []
    for x in links:
        if not isinstance(x, dict):
            continue
        label = str(x.get("label") or "").strip()
        href = str(x.get("href") or "").strip()
        if not label or not href:
            continue
        rows.append([_esc(label), f"<a href='{_esc(href)}'>{_esc(href)}</a>"])
    return _table_or_empty_html(["name", "url"], rows, empty_label="none")


def _build_related_timeline_links(alert_id: str) -> list[dict[str, str]]:
    aid = str(alert_id or "").strip()
    if not aid:
        return []
    q = quote_plus(aid)
    return [
        {"label": "Timeline JSON", "href": f"/api/mlops/research/scenario-router/ops/timeline/{q}"},
        {"label": "Timeline HTML", "href": f"/api/mlops/research/scenario-router/ops/timeline/{q}.html"},
    ]


def _render_timeline_preview_table(items: list[dict[str, Any]], *, max_rows: int = 8) -> str:
    rows_html: list[list[str]] = []
    for x in (items or [])[: max(1, int(max_rows))]:
        if not isinstance(x, dict):
            continue
        detail_url = str(x.get("detail_url") or "")
        detail_html = f"<a href='{_esc(detail_url)}'>detail</a>" if detail_url else ""
        rows_html.append(
            [
                _esc(str(x.get("timestamp") or "")),
                _esc(str(x.get("entity_type") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("summary") or "")),
                detail_html,
            ]
        )
    return _table_or_empty_html(["timestamp", "entity", "status", "summary", "detail"], rows_html, empty_label="none")


def _render_detail_page_html(
    *,
    page_title: str,
    item: dict[str, Any],
    item_id_key: str,
    key_fields: list[str],
    get_snippets: list[dict[str, str]],
    post_preview_snippets: list[dict[str, str]],
    manual_guide: dict[str, list[str]],
    related_links: list[dict[str, str]],
    related_timeline_links: list[dict[str, str]] | None = None,
    timeline_preview_items: list[dict[str, Any]] | None = None,
) -> str:
    item_id = str(item.get(item_id_key) or "")
    rows_html: list[list[str]] = []
    for k in key_fields:
        rows_html.append([_esc(k), _esc(item.get(k) if k in item else "")])
    kv_html = _table_or_empty_html(["field", "value"], rows_html, empty_label="none")
    raw_json = json.dumps(item, ensure_ascii=False, indent=2, default=str)

    get_blocks: list[str] = []
    for i, x in enumerate(get_snippets):
        if not isinstance(x, dict):
            continue
        get_blocks.append(_render_snippet_block(1000 + i, str(x.get("name") or ""), str(x.get("curl") or "")))

    safe_preview_snippets = [x for x in post_preview_snippets if isinstance(x, dict) and _is_safe_preview_snippet(x)]
    post_blocks: list[str] = []
    for i, x in enumerate(safe_preview_snippets):
        post_blocks.append(_render_snippet_block(1100 + i, str(x.get("name") or ""), str(x.get("curl") or "")))

    handoff_placeholder = "# Manual operation handoff only\n# execute/rollback/stop/resolve/disable/delete commands are intentionally not rendered in dashboard"

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{_esc(page_title)}</title>
  <style>
    :root {{ --line:#d9e2ec; --muted:#5e7188; }}
    body {{ margin:0; padding:20px; background:#f8fbff; color:#132238; font-family:Segoe UI, Arial, sans-serif; }}
    h1 {{ margin:0 0 12px; font-size:24px; }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px; margin:0 0 12px; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    ul {{ margin:6px 0 8px 18px; padding:0; }}
    li {{ margin:4px 0; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ background:#f8fbff; }}
    pre {{ margin:0; padding:10px; border-radius:8px; background:#0b1220; color:#d7e3ff; overflow-x:auto; border:1px solid #2b3a54; }}
    code {{ font-family:Consolas, 'Courier New', monospace; font-size:12px; }}
    .snippet-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
    .copy-btn {{ padding:4px 8px; border:1px solid #3762ac; border-radius:8px; color:#17386f; background:#eaf2ff; cursor:pointer; font-size:12px; font-weight:600; }}
    .copy-btn.copied {{ border-color:#0f9d58; color:#0f9d58; background:#e7f7ef; }}
    a {{ color:#1e4db7; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <div class=\"card\">
    <h1>{_esc(page_title)}</h1>
    <div class=\"muted\">{_esc(item_id_key)}: {_esc(item_id)}</div>
    <div style=\"margin-top:8px;\"><a href=\"/api/mlops/research/scenario-router/ops/dashboard.html\">Back to dashboard</a></div>
  </div>

  <div class=\"card\">
    <h2>Summary</h2>
    {kv_html}
  </div>

  <div class=\"card\">
    <h2>Raw JSON</h2>
    <pre><code>{_esc(raw_json)}</code></pre>
  </div>

  <div class=\"card\">
    <h2>Safe Read-only Curl</h2>
    {''.join(get_blocks)}
  </div>

  <div class=\"card\">
    <h2>Action Preview Curl</h2>
    <div class=\"muted\" style=\"margin-bottom:8px;\">Manual approval required. Dangerous actions are intentionally not rendered.</div>
    {''.join(post_blocks)}
    <details>
      <summary>Manual operation handoff examples (disabled)</summary>
      <div class=\"muted\" style=\"margin:8px 0;\">These commands are intentionally omitted to keep dashboard read-only.</div>
      <pre><code>{_esc(handoff_placeholder)}</code></pre>
    </details>
  </div>

  <div class=\"card\">
    <h2>Manual Approval Guide</h2>
    <h3 style=\"margin:0 0 6px; font-size:15px;\">Recommended next checks</h3>
    {_render_text_list(list(manual_guide.get('recommended_next_checks') or []), empty_label='none')}
    <h3 style=\"margin:10px 0 6px; font-size:15px;\">Safe preview commands</h3>
    {_render_text_list(list(manual_guide.get('safe_preview_commands') or []), empty_label='none')}
    <h3 style=\"margin:10px 0 6px; font-size:15px;\">Manual approval required actions</h3>
    {_render_text_list(list(manual_guide.get('manual_approval_required_actions') or []), empty_label='none')}
    <div class=\"muted\" style=\"margin-top:8px;\">Dangerous operations intentionally not rendered.</div>
  </div>

    <div class=\"card\">
        <h2>Related Timeline</h2>
        {_render_related_links(list(related_timeline_links or []))}
        <h3 style=\"margin:10px 0 6px; font-size:15px;\">Recent timeline items</h3>
        {_render_timeline_preview_table(list(timeline_preview_items or []), max_rows=8)}
    </div>

    <div class=\"card\">
        <h2>Related pages</h2>
        {_render_related_links(related_links)}
    <h3 style=\"margin:10px 0 6px; font-size:15px;\">Suggested follow-up check</h3>
    {_render_text_list(list(manual_guide.get('suggested_follow_up_checks') or []), empty_label='none')}
    <div style=\"margin-top:8px;\"><a href=\"/api/mlops/research/scenario-router/ops/dashboard.html\">Back to dashboard</a></div>
  </div>
  {_render_copy_script()}
</body>
</html>
"""


def render_not_found_detail_html(*, page_title: str, item_id: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\" /><title>{_esc(page_title)} Not Found</title></head>
<body style=\"font-family:Segoe UI, Arial, sans-serif; padding:20px;\">
  <h1>{_esc(page_title)} Not Found</h1>
  <p>ID: {_esc(item_id)}</p>
  <p><a href=\"/api/mlops/research/scenario-router/ops/dashboard.html\">Back to dashboard</a></p>
</body></html>"""


def render_ops_alert_detail_html(*, alert_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_router_alert_by_id(alert_id=str(alert_id))
    if not item:
        return render_not_found_detail_html(page_title="Alert Detail", item_id=str(alert_id)), 404
    target = str(item.get("target") or "win")
    aid = str(item.get("alert_id") or alert_id)
    related_links = [
        {"label": "Alert list", "href": f"/api/mlops/research/scenario-router/alerts?target={quote_plus(target)}&status=open&limit=20"},
        {"label": "Runbooks for alert", "href": f"/api/mlops/research/scenario-router/runbooks?target={quote_plus(target)}&alert_id={quote_plus(aid)}&limit=20"},
        {"label": "Incident responses for alert", "href": f"/api/mlops/research/scenario-router/incidents/responses?target={quote_plus(target)}&alert_id={quote_plus(aid)}&limit=20"},
        {"label": "Auto recovery executions for alert", "href": f"/api/mlops/research/scenario-router/auto-recovery/executions?alert_id={quote_plus(aid)}&limit=20"},
    ]
    get_snippets = [
        {
            "name": "GET Alerts",
            "curl": f"curl -sS \"${{API_BASE_URL:-http://127.0.0.1:8000}}/api/mlops/research/scenario-router/alerts?target={quote_plus(target)}&status=open&limit=20\"",
        }
    ]
    timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
    timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []
    html = _render_detail_page_html(
        page_title="Alert Detail",
        item=item,
        item_id_key="alert_id",
        key_fields=["alert_id", "target", "severity", "alert_type", "status", "title", "decision", "action", "created_at", "resolved_at"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("alert", item),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_ops_runbook_detail_html(*, runbook_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_router_runbook_by_id(runbook_id=str(runbook_id))
    if not item:
        return render_not_found_detail_html(page_title="Runbook Detail", item_id=str(runbook_id)), 404
    target = str(item.get("target") or "win")
    aid = str(item.get("alert_id") or "")
    rid = str(item.get("runbook_id") or runbook_id)
    related_links = [
        {"label": "Runbook API", "href": f"/api/mlops/research/scenario-router/runbooks/{quote_plus(rid)}"},
        {"label": "Alert detail", "href": _detail_href("alerts", aid)} if aid else {},
        {"label": "Incident actions (by runbook)", "href": f"/api/mlops/research/scenario-router/incidents/actions?target={quote_plus(target)}&runbook_id={quote_plus(rid)}&limit=20"},
        {"label": "Incident responses (by alert)", "href": f"/api/mlops/research/scenario-router/incidents/responses?target={quote_plus(target)}&alert_id={quote_plus(aid)}&limit=20"} if aid else {},
    ]
    get_snippets = [
        {
            "name": "GET Runbook",
            "curl": f"curl -sS \"${{API_BASE_URL:-http://127.0.0.1:8000}}/api/mlops/research/scenario-router/runbooks/{quote_plus(str(runbook_id))}\"",
        }
    ]
    timeline_items: list[dict[str, Any]] = []
    if aid:
        timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
        timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []
    html = _render_detail_page_html(
        page_title="Runbook Detail",
        item=item,
        item_id_key="runbook_id",
        key_fields=["runbook_id", "alert_id", "target", "severity", "alert_type", "title", "created_at"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("runbook", item),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_ops_response_detail_html(*, response_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_incident_response_by_id(response_id=str(response_id))
    if not item:
        return render_not_found_detail_html(page_title="Incident Response Detail", item_id=str(response_id)), 404
    target = str(item.get("target") or "win")
    aid = str(item.get("alert_id") or "")
    rid = str(item.get("runbook_id") or "")
    respid = str(item.get("response_id") or response_id)
    related_links = [
        {"label": "Incident response API", "href": f"/api/mlops/research/scenario-router/incidents/responses/{quote_plus(respid)}"},
        {"label": "Alert detail", "href": _detail_href("alerts", aid)} if aid else {},
        {"label": "Runbook detail", "href": _detail_href("runbooks", rid)} if rid else {},
        {"label": "Auto recovery executions for response", "href": f"/api/mlops/research/scenario-router/auto-recovery/executions?response_id={quote_plus(respid)}&limit=20"},
    ]
    get_snippets = [
        {
            "name": "GET Incident Response",
            "curl": f"curl -sS \"${{API_BASE_URL:-http://127.0.0.1:8000}}/api/mlops/research/scenario-router/incidents/responses/{quote_plus(str(response_id))}\"",
        }
    ]
    timeline_items: list[dict[str, Any]] = []
    if aid:
        timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
        timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []
    html = _render_detail_page_html(
        page_title="Incident Response Detail",
        item=item,
        item_id_key="response_id",
        key_fields=["response_id", "alert_id", "runbook_id", "target", "severity", "status", "created_at", "updated_at"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("response", item),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_ops_action_detail_html(*, action_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_incident_action_by_id(action_id=str(action_id))
    if not item:
        return render_not_found_detail_html(page_title="Incident Action Detail", item_id=str(action_id)), 404
    target = str(item.get("target") or "win")
    aid = str(item.get("alert_id") or "")
    rid = str(item.get("runbook_id") or "")
    related_links = [
        {"label": "Incident actions list", "href": f"/api/mlops/research/scenario-router/incidents/actions?target={quote_plus(target)}&alert_id={quote_plus(aid)}&runbook_id={quote_plus(rid)}&limit=20"},
        {"label": "Alert detail", "href": _detail_href("alerts", aid)} if aid else {},
        {"label": "Runbook detail", "href": _detail_href("runbooks", rid)} if rid else {},
        {"label": "Incident responses (by alert)", "href": f"/api/mlops/research/scenario-router/incidents/responses?target={quote_plus(target)}&alert_id={quote_plus(aid)}&limit=20"} if aid else {},
    ]
    get_snippets = [
        {
            "name": "GET Incident Actions",
            "curl": f"curl -sS \"${{API_BASE_URL:-http://127.0.0.1:8000}}/api/mlops/research/scenario-router/incidents/actions?target={quote_plus(target)}&limit=20\"",
        }
    ]
    timeline_items: list[dict[str, Any]] = []
    if aid:
        timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
        timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []
    html = _render_detail_page_html(
        page_title="Incident Action Detail",
        item=item,
        item_id_key="action_id",
        key_fields=["action_id", "alert_id", "runbook_id", "target", "action_type", "status", "dry_run", "created_at", "executed_at"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("action", item),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_ops_auto_recovery_execution_detail_html(*, execution_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_auto_recovery_execution_by_id(execution_id=str(execution_id))
    if not item:
        return render_not_found_detail_html(page_title="Auto Recovery Execution Detail", item_id=str(execution_id)), 404
    target = "win"
    aid = str(item.get("alert_id") or "")
    respid = str(item.get("response_id") or "")
    related_links = [
        {"label": "Auto recovery executions list", "href": f"/api/mlops/research/scenario-router/auto-recovery/executions?response_id={quote_plus(respid)}&alert_id={quote_plus(aid)}&limit=20"},
        {"label": "Alert detail", "href": _detail_href("alerts", aid)} if aid else {},
        {"label": "Incident response detail", "href": _detail_href("responses", respid)} if respid else {},
    ]
    get_snippets = [
        {
            "name": "GET Auto Recovery Executions",
            "curl": "curl -sS \"${API_BASE_URL:-http://127.0.0.1:8000}/api/mlops/research/scenario-router/auto-recovery/executions?limit=20\"",
        }
    ]
    timeline_items: list[dict[str, Any]] = []
    if aid:
        timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
        timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []
    html = _render_detail_page_html(
        page_title="Auto Recovery Execution Detail",
        item=item,
        item_id_key="execution_id",
        key_fields=["execution_id", "response_id", "alert_id", "action_type", "status", "auto_executed", "manual_required", "created_at", "executed_at"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("auto_recovery_execution", item),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_ops_notification_delivery_detail_html(*, delivery_id: str, store: MLOpsStore | None = None) -> tuple[str, int]:
    s = store or MLOpsStore()
    item = s.get_router_notification_delivery_by_id(delivery_id=str(delivery_id))
    if not item:
        return render_not_found_detail_html(page_title="Notification Delivery Detail", item_id=str(delivery_id)), 404

    target = str(item.get("target") or "win")
    aid = str(item.get("alert_id") or "")
    did = str(item.get("delivery_id") or delivery_id)
    related_links = [
        {
            "label": "Notification deliveries list",
            "href": f"/api/mlops/research/scenario-router/notifications/deliveries?target={quote_plus(target)}&alert_id={quote_plus(aid)}&limit=20",
        },
        {"label": "Alert detail", "href": _detail_href("alerts", aid)} if aid else {},
    ]
    get_snippets = [
        {
            "name": "GET Notification Deliveries",
            "curl": f"curl -sS \"${{API_BASE_URL:-http://127.0.0.1:8000}}/api/mlops/research/scenario-router/notifications/deliveries?target={quote_plus(target)}&limit=20\"",
        }
    ]
    timeline_items: list[dict[str, Any]] = []
    if aid:
        timeline_data = get_scenario_router_incident_timeline(alert_id=aid, target=target, limit=120, store=s)
        timeline_items = timeline_data.get("items") if isinstance(timeline_data.get("items"), list) else []

    html = _render_detail_page_html(
        page_title="Notification Delivery Detail",
        item=item,
        item_id_key="delivery_id",
        key_fields=["delivery_id", "alert_id", "target", "channel_id", "status", "attempt_count", "sent_at", "created_at", "last_error"],
        get_snippets=get_snippets,
        post_preview_snippets=_build_action_preview_post_snippets(target=target, limit=20),
        manual_guide=_build_manual_approval_guide("response", {"severity": "INFO", "status": str(item.get("status") or "")}),
        related_links=related_links,
        related_timeline_links=_build_related_timeline_links(aid),
        timeline_preview_items=timeline_items,
    )
    return html, 200


def render_scenario_router_ops_timeline_html(
    *,
    alert_id: str,
    target: str = "win",
    limit: int = 50,
    offset: int = 0,
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    sort: str = "desc",
    store: MLOpsStore | None = None,
) -> tuple[str, int]:
    data = get_scenario_router_incident_timeline(
        alert_id=str(alert_id),
        target=target,
        limit=limit,
        offset=offset,
        entity_type=entity_type,
        status=status,
        since=since,
        until=until,
        sort=sort,
        store=store,
    )
    aid = str(data.get("alert_id") or "")
    not_found = bool(data.get("not_found"))
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    counts = summary.get("counts_by_entity") if isinstance(summary.get("counts_by_entity"), dict) else {}
    pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}

    q_entity_type = str(filters.get("entity_type") or "all")
    q_status = str(filters.get("status") or "")
    q_since = str(filters.get("since") or "")
    q_until = str(filters.get("until") or "")
    q_sort = str(filters.get("sort") or "desc")
    q_limit = max(1, min(_safe_int(pagination.get("limit"), limit), 200))
    q_offset = max(0, _safe_int(pagination.get("offset"), offset))
    invalid_filters = list(filters.get("invalid") or [])

    def _timeline_query(off: int) -> str:
        return (
            f"entity_type={quote_plus(q_entity_type)}"
            + f"&status={quote_plus(q_status)}"
            + f"&since={quote_plus(q_since)}"
            + f"&until={quote_plus(q_until)}"
            + f"&sort={quote_plus(q_sort)}"
            + f"&limit={q_limit}"
            + f"&offset={max(0, int(off))}"
        )

    base_html = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html"
    base_export = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/export"
    prev_offset = max(0, q_offset - q_limit)
    next_offset = max(0, _safe_int(pagination.get("next_offset"), q_offset))
    has_more = bool(pagination.get("has_more"))
    prev_href = f"{base_html}?{_timeline_query(prev_offset)}"
    next_href = f"{base_html}?{_timeline_query(next_offset)}"
    export_md_href = f"{base_export}?format=markdown&{_timeline_query(q_offset)}"
    export_csv_href = f"{base_export}?format=csv&{_timeline_query(q_offset)}"
    export_json_href = f"{base_export}?format=json&{_timeline_query(q_offset)}"
    report_md_href = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/report?format=markdown&style=default&{_timeline_query(q_offset)}"
    report_notion_href = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/report?format=markdown&style=notion&{_timeline_query(q_offset)}"

    count_rows = []
    for k in [
        "alert",
        "runbook",
        "incident_response",
        "incident_action",
        "auto_recovery_execution",
        "notification_delivery",
    ]:
        count_rows.append([k, int(_safe_int(counts.get(k), 0))])

    item_rows: list[list[str]] = []
    items = data.get("items") if isinstance(data.get("items"), list) else []
    for x in items:
        if not isinstance(x, dict):
            continue
        detail_url = str(x.get("detail_url") or "")
        detail_html = f"<a href='{_esc(detail_url)}'>detail</a>" if detail_url else ""
        item_rows.append(
            [
                _esc(str(x.get("timestamp") or "")),
                _esc(str(x.get("entity_type") or "")),
                _esc(str(x.get("entity_id") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("severity") or "")),
                _esc(str(x.get("action_type") or "")),
                _esc(str(x.get("summary") or "")),
                detail_html,
                _esc(bool(x.get("is_dangerous"))),
                _esc(str(x.get("read_only_note") or "")),
            ]
        )

    not_found_banner = ""
    if not_found:
        not_found_banner = (
            "<div class='card'><h2>Alert Not Found</h2>"
            + f"<div class='muted'>alert_id={_esc(aid)}</div>"
            + "<div class='muted'>No related timeline found. This page remains read-only.</div></div>"
        )

    invalid_banner = ""
    if invalid_filters:
        invalid_banner = (
            "<div class='card'><h2>Invalid Filters</h2>"
            + f"<div class='muted'>Ignored invalid filters: {_esc(', '.join(invalid_filters))}</div></div>"
        )

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Incident Timeline</title>
  <style>
    :root {{ --line:#d9e2ec; --muted:#5e7188; }}
    body {{ margin:0; padding:20px; background:#f8fbff; color:#132238; font-family:Segoe UI, Arial, sans-serif; }}
    h1 {{ margin:0 0 12px; font-size:24px; }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px; margin:0 0 12px; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .filter-form {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; margin:8px 0 6px; }}
    .filter-form label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .filter-form input, .filter-form select {{ width:100%; padding:7px 8px; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    .filter-form button {{ padding:8px 10px; border:1px solid #204b98; border-radius:8px; color:#fff; background:#2f67c6; cursor:pointer; }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ background:#f8fbff; }}
    a {{ color:#1e4db7; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <div class=\"card\">
    <h1>Incident Timeline</h1>
    <div class=\"muted\">alert_id: {_esc(aid)}</div>
    <div class=\"muted\">target: {_esc(str(data.get('target') or ''))}</div>
        <div class=\"muted\">Read-only note: Timeline is investigation-only. Manual approval path is required for any execution.</div>
    <div style=\"margin-top:8px;\"><a href=\"/api/mlops/research/scenario-router/ops/dashboard.html\">Back to dashboard</a></div>
        <div style=\"margin-top:8px;\"><a href=\"{_esc(export_md_href)}\">Export Markdown</a> | <a href=\"{_esc(export_csv_href)}\">Export CSV</a> | <a href=\"{_esc(export_json_href)}\">Export JSON</a></div>
        <div style=\"margin-top:8px;\"><a href=\"{_esc(report_md_href)}\">Report Markdown</a> | <a href=\"{_esc(report_notion_href)}\">Report Notion</a></div>
  </div>
  {not_found_banner}
    {invalid_banner}
    <div class=\"card\">
        <h2>Filters</h2>
        <form method=\"get\" action=\"{_esc(base_html)}\" class=\"filter-form\">
            <div>
                <label for=\"entity_type\">entity_type</label>
                <select id=\"entity_type\" name=\"entity_type\">
                    <option value=\"all\" {('selected' if q_entity_type == 'all' else '')}>all</option>
                    <option value=\"alert\" {('selected' if q_entity_type == 'alert' else '')}>alert</option>
                    <option value=\"runbook\" {('selected' if q_entity_type == 'runbook' else '')}>runbook</option>
                    <option value=\"response\" {('selected' if q_entity_type == 'response' else '')}>response</option>
                    <option value=\"action\" {('selected' if q_entity_type == 'action' else '')}>action</option>
                    <option value=\"auto_recovery_execution\" {('selected' if q_entity_type == 'auto_recovery_execution' else '')}>auto_recovery_execution</option>
                    <option value=\"notification_delivery\" {('selected' if q_entity_type == 'notification_delivery' else '')}>notification_delivery</option>
                </select>
            </div>
            <div>
                <label for=\"status\">status</label>
                <input id=\"status\" name=\"status\" value=\"{_esc(q_status)}\" />
            </div>
            <div>
                <label for=\"since\">since (ISO8601 / YYYY-MM-DD)</label>
                <input id=\"since\" name=\"since\" value=\"{_esc(q_since)}\" />
            </div>
            <div>
                <label for=\"until\">until (ISO8601 / YYYY-MM-DD)</label>
                <input id=\"until\" name=\"until\" value=\"{_esc(q_until)}\" />
            </div>
            <div>
                <label for=\"sort\">sort</label>
                <select id=\"sort\" name=\"sort\">
                    <option value=\"desc\" {('selected' if q_sort == 'desc' else '')}>desc</option>
                    <option value=\"asc\" {('selected' if q_sort == 'asc' else '')}>asc</option>
                </select>
            </div>
            <div>
                <label for=\"limit\">limit (max 200)</label>
                <input id=\"limit\" name=\"limit\" value=\"{_esc(q_limit)}\" />
            </div>
            <div>
                <label for=\"offset\">offset</label>
                <input id=\"offset\" name=\"offset\" value=\"{_esc(q_offset)}\" />
            </div>
            <div style=\"display:flex; align-items:flex-end;\">
                <button type=\"submit\">Apply</button>
            </div>
        </form>
        <div class=\"muted\">Applied filters: entity_type={_esc(q_entity_type)} | status={_esc(q_status)} | since={_esc(q_since)} | until={_esc(q_until)} | sort={_esc(q_sort)}</div>
        <div class=\"muted\">Pagination: limit={_esc(q_limit)} | offset={_esc(q_offset)} | returned={_esc(_safe_int(pagination.get('returned'), 0))} | has_more={_esc(has_more)}</div>
    </div>
  <div class=\"card\">
    <h2>Chain Summary</h2>
    {_table_or_empty(['entity_type', 'count'], count_rows, empty_label='none')}
  </div>
  <div class=\"card\">
    <h2>Timeline Items</h2>
    {_table_or_empty_html(['timestamp', 'entity_type', 'entity_id', 'status', 'severity', 'action_type', 'summary', 'detail', 'is_dangerous', 'read_only_note'], item_rows, empty_label='none')}
        <div style=\"margin-top:10px;\">
            <a href=\"{_esc(prev_href)}\">Prev</a>
            <span class=\"muted\" style=\"margin:0 8px;\">offset={_esc(q_offset)}</span>
            {f"<a href='{_esc(next_href)}'>Next</a>" if has_more else "<span class='muted'>Next</span>"}
        </div>
  </div>
</body>
</html>
"""
    return html, 200


def render_scenario_router_ops_dashboard_html(
    *,
    target: str = "win",
    reports_dir: str | None = None,
    limit: int = 10,
    refresh_sec: int = 0,
    show_raw_links: bool = False,
) -> str:
    n = _normalize_limit(limit, default=10, max_value=100)
    refresh = _normalize_refresh(refresh_sec, default=0, max_value=3600)
    raw_links = _normalize_bool(show_raw_links, default=False)
    target_display, _ = _normalize_target_filter(target)

    data = get_scenario_router_ops_dashboard(target=target_display, reports_dir=reports_dir, limit=n)

    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    rollout = data.get("rollout") if isinstance(data.get("rollout"), dict) else {}
    alerts = data.get("alerts") if isinstance(data.get("alerts"), dict) else {}
    incidents = data.get("incidents") if isinstance(data.get("incidents"), dict) else {}
    notifications = data.get("notifications") if isinstance(data.get("notifications"), dict) else {}
    incident_timeline = data.get("incident_timeline") if isinstance(data.get("incident_timeline"), dict) else {}
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}

    query_target = _esc(target_display)
    query_limit = _esc(n)
    query_refresh = _esc(refresh)
    query_raw = _esc(str(raw_links).lower())
    dashboard_json_link = (
        f"/api/mlops/research/scenario-router/ops/dashboard?target={query_target}&limit={query_limit}&refresh={query_refresh}&show_raw_links={query_raw}"
    )
    audit_latest_link = "/api/mlops/research/scenario-router/ops/audit/latest"
    audit_history_link = f"/api/mlops/research/scenario-router/ops/audit/history?limit={query_limit}"
    incidents_latest_link = f"/api/mlops/research/scenario-router/ops/incidents/latest?target={query_target}&limit={query_limit}"

    refresh_meta = (
        f"<meta http-equiv='refresh' content='{refresh}' />" if refresh > 0 else ""
    )

    snippets = _build_readonly_curl_snippets(
        target=target_display,
        limit=n,
        refresh=refresh,
        show_raw_links=raw_links,
    )
    action_preview_snippets = _build_action_preview_post_snippets(
        target=target_display,
        limit=n,
    )
    snippet_blocks: list[str] = []
    for i, x in enumerate(snippets):
        if not isinstance(x, dict):
            continue
        snippet_blocks.append(
            _render_snippet_block(
                index=i,
                name=str(x.get("name") or ""),
                curl_cmd=str(x.get("curl") or ""),
            )
        )
    snippets_html = "".join(snippet_blocks)

    action_preview_blocks: list[str] = []
    for i, x in enumerate(action_preview_snippets):
        if not isinstance(x, dict):
            continue
        action_preview_blocks.append(
            _render_snippet_block(
                index=100 + i,
                name=str(x.get("name") or ""),
                curl_cmd=str(x.get("curl") or ""),
            )
        )
    action_preview_html = "".join(action_preview_blocks)

    baseline_eval = audit.get("baseline_evaluation") if isinstance(audit.get("baseline_evaluation"), list) else []
    baseline_rows: list[list[Any]] = []
    for x in baseline_eval[:10]:
        if not isinstance(x, dict):
            continue
        baseline_rows.append(
            [
                str(x.get("step_name") or ""),
                str(x.get("status") or ""),
                f"{_safe_float(x.get('current_duration_sec'), 0.0):.2f}",
                f"{_safe_float(x.get('median_duration_sec'), 0.0):.2f}",
                f"{_safe_float(x.get('warn_threshold_sec'), 0.0):.2f}",
                f"{_safe_float(x.get('fail_threshold_sec'), 0.0):.2f}",
            ]
        )

    stderr_trend = audit.get("stderr_trend") if isinstance(audit.get("stderr_trend"), dict) else {}
    latest_stderr_classification = str(stderr_trend.get("latest_stderr_classification") or "NO_STDERR")
    last_10_stderr_noise_rate = _safe_float(stderr_trend.get("last_10_stderr_noise_rate"), 0.0)
    last_10_real_stderr_rate = _safe_float(stderr_trend.get("last_10_real_stderr_rate"), 0.0)
    stderr_suggested_next_action = str(stderr_trend.get("suggested_next_action") or "")
    stderr_common_html = _render_top_name_count(
        stderr_trend.get("common_stderr_classifications"),
        name_key="classification",
        count_key="count",
        top_n=8,
    )
    stderr_noisy_steps_html = _render_top_name_count(
        stderr_trend.get("noisy_steps"),
        name_key="step_name",
        count_key="count",
        top_n=8,
    )
    stderr_real_error_steps_html = _render_top_name_count(
        stderr_trend.get("real_error_steps"),
        name_key="step_name",
        count_key="count",
        top_n=8,
    )

    latest_alerts = alerts.get("latest") if isinstance(alerts.get("latest"), list) else []
    alert_rows = []
    for a in latest_alerts[:10]:
        if not isinstance(a, dict):
            continue
        alert_id = str(a.get("alert_id") or "")
        detail = f"<a href='{_esc(_detail_href('alerts', alert_id))}'>detail</a>" if alert_id else ""
        alert_rows.append(
            [
                _esc(str(a.get("created_at") or "")),
                _esc(str(a.get("severity") or "")),
                _esc(str(a.get("alert_type") or "")),
                _esc(str(a.get("status") or "")),
                _esc(str(a.get("title") or "")),
                detail,
            ]
        )

    runbook_rows = []
    for x in (incidents.get("latest_runbooks") if isinstance(incidents.get("latest_runbooks"), list) else [])[:10]:
        if not isinstance(x, dict):
            continue
        runbook_id = str(x.get("runbook_id") or "")
        detail = f"<a href='{_esc(_detail_href('runbooks', runbook_id))}'>detail</a>" if runbook_id else ""
        runbook_rows.append(
            [
                _esc(str(x.get("created_at") or "")),
                _esc(str(x.get("severity") or "")),
                _esc(str(x.get("alert_type") or "")),
                _esc(str(x.get("title") or "")),
                detail,
            ]
        )

    response_rows = []
    for x in (incidents.get("latest_responses") if isinstance(incidents.get("latest_responses"), list) else [])[:10]:
        if not isinstance(x, dict):
            continue
        response_id = str(x.get("response_id") or "")
        detail = f"<a href='{_esc(_detail_href('responses', response_id))}'>detail</a>" if response_id else ""
        response_rows.append(
            [
                _esc(str(x.get("updated_at") or x.get("created_at") or "")),
                _esc(str(x.get("severity") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("response_id") or "")),
                detail,
            ]
        )

    action_rows = []
    for x in (incidents.get("latest_actions") if isinstance(incidents.get("latest_actions"), list) else [])[:10]:
        if not isinstance(x, dict):
            continue
        action_id = str(x.get("action_id") or "")
        detail = f"<a href='{_esc(_detail_href('actions', action_id))}'>detail</a>" if action_id else ""
        action_rows.append(
            [
                _esc(str(x.get("created_at") or "")),
                _esc(str(x.get("action_type") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("dry_run") or "")),
                detail,
            ]
        )

    recovery_rows = []
    for x in (
        incidents.get("latest_auto_recovery_executions")
        if isinstance(incidents.get("latest_auto_recovery_executions"), list)
        else []
    )[:10]:
        if not isinstance(x, dict):
            continue
        execution_id = str(x.get("execution_id") or "")
        detail = f"<a href='{_esc(_detail_href('auto-recovery/executions', execution_id))}'>detail</a>" if execution_id else ""
        recovery_rows.append(
            [
                _esc(str(x.get("created_at") or "")),
                _esc(str(x.get("action_type") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("manual_required") or "")),
                detail,
            ]
        )

    delivery_rows = []
    for x in (notifications.get("latest_deliveries") if isinstance(notifications.get("latest_deliveries"), list) else [])[:10]:
        if not isinstance(x, dict):
            continue
        delivery_rows.append(
            [
                str(x.get("created_at") or ""),
                str(x.get("channel_id") or ""),
                str(x.get("status") or ""),
                str(x.get("alert_id") or ""),
            ]
        )

    timeline_rows_html: list[list[str]] = []
    for x in (incident_timeline.get("items") if isinstance(incident_timeline.get("items"), list) else [])[:10]:
        if not isinstance(x, dict):
            continue
        aid = str(x.get("alert_id") or "")
        alert_detail = f"<a href='{_esc(_detail_href('alerts', aid))}'>alert</a>" if aid else ""
        timeline_url = str(x.get("timeline_url") or "")
        timeline_html_url = str(x.get("timeline_html_url") or "")
        timeline_links = ""
        if timeline_url or timeline_html_url:
            a_json = f"<a href='{_esc(timeline_url)}'>json</a>" if timeline_url else ""
            a_html = f"<a href='{_esc(timeline_html_url)}'>html</a>" if timeline_html_url else ""
            timeline_links = " / ".join([z for z in [a_json, a_html] if z])
        filtered_link = ""
        if aid:
            filter_href = (
                f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}.html"
                + "?entity_type=action&status=FAILED&sort=desc&limit=50&offset=0"
            )
            filtered_link = (
                f"<a href='{_esc(filter_href)}'>"
                + "action/failed filter</a>"
            )
        export_link = ""
        if aid:
            export_href = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/export?format=markdown&limit=50&offset=0&sort=desc"
            export_link = f"<a href='{_esc(export_href)}'>md</a>"
        report_link = ""
        if aid:
            report_href = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/report?format=markdown&style=default&limit=50&offset=0&sort=desc"
            report_notion_href = f"/api/mlops/research/scenario-router/ops/timeline/{quote_plus(aid)}/report?format=markdown&style=notion&limit=50&offset=0&sort=desc"
            report_link = f"<a href='{_esc(report_href)}'>md report</a> / <a href='{_esc(report_notion_href)}'>notion</a>"
        timeline_rows_html.append(
            [
                _esc(str(x.get("created_at") or "")),
                _esc(str(x.get("severity") or "")),
                _esc(str(x.get("status") or "")),
                _esc(str(x.get("title") or "")),
                _esc(int(_safe_int(x.get("event_count"), 0))),
                _esc(str(x.get("latest_timestamp") or "")),
                _esc(str(x.get("latest_entity_type") or "")),
                _esc(str(x.get("latest_status") or "")),
                alert_detail,
                timeline_links,
                filtered_link,
                export_link,
                report_link,
            ]
        )

    artifact_rows = []
    for k in ["result", "gate", "history", "trend"]:
        m = artifacts.get(k) if isinstance(artifacts.get(k), dict) else {}
        artifact_rows.append(
            [
                k,
                bool(m.get("missing")),
                bool(m.get("decode_error")),
                str(m.get("error") or ""),
            ]
        )

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    {refresh_meta}
  <title>Scenario Router Ops Dashboard</title>
  <style>
    :root {{ --bg:#f4f7fb; --card:#ffffff; --txt:#132238; --muted:#5e7188; --line:#d9e2ec; --pass:#0f9d58; --warn:#e37400; --fail:#c62828; --info:#4f6fad; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:20px; background:linear-gradient(180deg,#edf3fb 0%,#f8fbff 100%); color:var(--txt); font-family:Segoe UI, Arial, sans-serif; }}
    h1 {{ margin:0 0 16px 0; font-size:24px; }}
    h2 {{ margin:0 0 10px 0; font-size:18px; }}
    .grid {{ display:grid; gap:12px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; box-shadow:0 2px 8px rgba(0,0,0,0.04); }}
    .kv {{ display:grid; grid-template-columns:180px 1fr; gap:6px 10px; font-size:14px; }}
    .k {{ color:var(--muted); }}
    .status {{ display:inline-block; padding:3px 8px; border-radius:999px; font-weight:700; font-size:12px; }}
    .status-pass {{ background:#e7f7ef; color:var(--pass); }}
    .status-warn {{ background:#fff2e5; color:var(--warn); }}
    .status-fail {{ background:#fdeaea; color:var(--fail); }}
    .status-info {{ background:#eaf0ff; color:var(--info); }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ background:#f8fbff; color:#334e68; font-weight:700; }}
    .muted {{ color:var(--muted); font-size:13px; }}
    .full {{ grid-column: 1 / -1; }}
        .links a {{ color:#1e4db7; text-decoration:none; margin-right:10px; }}
        .links a:hover {{ text-decoration:underline; }}
        .filter-form {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px; margin:8px 0 6px; }}
        .filter-form label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
        .filter-form input, .filter-form select {{ width:100%; padding:7px 8px; border:1px solid var(--line); border-radius:8px; background:#fff; }}
        .filter-form button {{ padding:8px 10px; border:1px solid #204b98; border-radius:8px; color:#fff; background:#2f67c6; cursor:pointer; }}
        pre {{ margin:0; padding:10px; border-radius:8px; background:#0b1220; color:#d7e3ff; overflow-x:auto; border:1px solid #2b3a54; }}
        code {{ font-family:Consolas, 'Courier New', monospace; font-size:12px; }}
        .snippet-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
        .copy-btn {{ padding:4px 8px; border:1px solid #3762ac; border-radius:8px; color:#17386f; background:#eaf2ff; cursor:pointer; font-size:12px; font-weight:600; }}
        .copy-btn:hover {{ background:#dbe9ff; }}
        .copy-btn.copied {{ border-color:#0f9d58; color:#0f9d58; background:#e7f7ef; }}
  </style>
</head>
<body>
  <h1>Scenario Router Ops Dashboard</h1>
    <div class=\"card\" style=\"margin-bottom:12px;\">
        <h2>Filters</h2>
        <form method=\"get\" action=\"/api/mlops/research/scenario-router/ops/dashboard.html\" class=\"filter-form\">
            <div>
                <label for=\"target\">target</label>
                <input id=\"target\" name=\"target\" value=\"{query_target}\" />
            </div>
            <div>
                <label for=\"limit\">limit (1-100)</label>
                <input id=\"limit\" name=\"limit\" value=\"{query_limit}\" />
            </div>
            <div>
                <label for=\"refresh\">refresh (sec)</label>
                <input id=\"refresh\" name=\"refresh\" value=\"{query_refresh}\" />
            </div>
            <div>
                <label for=\"show_raw_links\">show_raw_links</label>
                <select id=\"show_raw_links\" name=\"show_raw_links\">
                    <option value=\"false\" {('selected' if not raw_links else '')}>false</option>
                    <option value=\"true\" {('selected' if raw_links else '')}>true</option>
                </select>
            </div>
            <div style=\"display:flex; align-items:flex-end;\">
                <button type=\"submit\">Apply</button>
            </div>
        </form>
        <div class=\"kv\" style=\"margin-top:8px;\">
            <div class=\"k\">Applied target</div><div>{query_target}</div>
            <div class=\"k\">Applied limit</div><div>{query_limit}</div>
            <div class=\"k\">Auto refresh</div><div>{_esc((f'{refresh}s' if refresh > 0 else 'disabled'))}</div>
            <div class=\"k\">show_raw_links</div><div>{_esc(raw_links)}</div>
        </div>
        <div class=\"links\" style=\"margin-top:8px;\">
            <a href=\"{dashboard_json_link}\">JSON Dashboard</a>
            <a href=\"{audit_latest_link}\">Audit Latest</a>
            <a href=\"{audit_history_link}\">Audit History</a>
            <a href=\"{incidents_latest_link}\">Incidents Latest</a>
        </div>
        {f"<div class='muted' style='margin-top:6px;'>Raw links: {_esc(dashboard_json_link)} | {_esc(audit_latest_link)} | {_esc(audit_history_link)} | {_esc(incidents_latest_link)}</div>" if raw_links else ""}
    </div>
    <section class=\"card\" style=\"margin-bottom:12px;\">
        <h2>API Operations</h2>
        <div class=\"muted\" style=\"margin-bottom:8px;\">Read-only GET snippets only. Manual approval required for state-changing operations, so they are intentionally not rendered for safety.</div>
        {snippets_html}
    </section>
    <section class=\"card\" style=\"margin-bottom:12px;\">
        <h2>Action Preview Snippets</h2>
        <details>
            <summary>Show read-only POST preview/evaluate/generate/test examples</summary>
            <div class=\"muted\" style=\"margin:8px 0;\">Dangerous actions are intentionally not rendered.</div>
            {action_preview_html}
        </details>
    </section>
  <div class=\"grid\">
    <section class=\"card\">
      <h2>Audit Summary</h2>
      <div class=\"kv\">
        <div class=\"k\">latest_status</div><div>{_status_badge(str(audit.get('latest_status') or ''))}</div>
        <div class=\"k\">gate_status</div><div>{_status_badge(str(audit.get('gate_status') or ''))}</div>
        <div class=\"k\">failure_type</div><div>{_esc(audit.get('failure_type') or 'NONE')}</div>
        <div class=\"k\">flaky_warning</div><div>{_esc(bool(audit.get('flaky_warning')))}</div>
        <div class=\"k\">last_10_success_rate</div><div>{_esc(_fmt_pct(audit.get('last_10_success_rate')))}</div>
        <div class=\"k\">applied_preset</div><div>{_esc(audit.get('applied_preset') or '')}</div>
      </div>
    </section>

    <section class=\"card\">
      <h2>Rollout Status</h2>
      <div class=\"kv\">
        <div class=\"k\">status</div><div>{_esc(rollout.get('status') or '')}</div>
        <div class=\"k\">current_percent</div><div>{_esc(rollout.get('current_percent') or 0)}</div>
        <div class=\"k\">previous_percent</div><div>{_esc(rollout.get('previous_percent') or 0)}</div>
        <div class=\"k\">router_mode</div><div>{_esc(rollout.get('router_mode') or '')}</div>
        <div class=\"k\">last_decision</div><div>{_esc(rollout.get('last_decision') or '')}</div>
      </div>
    </section>

        <section class=\"card\">
            <h2>Latest Alerts</h2>
      <div class=\"kv\">
        <div class=\"k\">open_count</div><div>{_esc(alerts.get('open_count') or 0)}</div>
      </div>
    {_table_or_empty_html(['created_at','severity','alert_type','status','title','detail'], alert_rows)}
    </section>

    <section class=\"card\">
      <h2>Suggested Next Action</h2>
      <div>{_esc(data.get('suggested_next_action') or '')}</div>
    </section>

    <section class=\"card full\">
      <h2>Gate / Baseline Summary</h2>
      {_table_or_empty(['step_name','status','current_sec','median_sec','warn_thr','fail_thr'], baseline_rows)}
    </section>

        <section class="card full">
            <h2>stderr Noise Trend</h2>
            <div class="kv">
                <div class="k">Latest classification</div><div>{_esc(latest_stderr_classification)}</div>
                <div class="k">Noise rate (last 10)</div><div>{_esc(_fmt_pct(last_10_stderr_noise_rate))}</div>
                <div class="k">Real error rate (last 10)</div><div>{_esc(_fmt_pct(last_10_real_stderr_rate))}</div>
                <div class="k">Suggested next action</div><div>{_esc(stderr_suggested_next_action)}</div>
            </div>
            <div class="grid" style="margin-top:10px;">
                <div class="card" style="padding:10px;">
                    <h3 style="margin:0 0 8px; font-size:15px;">Common classifications</h3>
                    {stderr_common_html}
                </div>
                <div class="card" style="padding:10px;">
                    <h3 style="margin:0 0 8px; font-size:15px;">Noisy steps</h3>
                    {stderr_noisy_steps_html}
                </div>
                <div class="card" style="padding:10px;">
                    <h3 style="margin:0 0 8px; font-size:15px;">Real error steps</h3>
                    {stderr_real_error_steps_html}
                </div>
            </div>
        </section>

    <section class=\"card full\">
      <h2>Latest Runbooks</h2>
    {_table_or_empty_html(['created_at','severity','alert_type','title','detail'], runbook_rows)}
    </section>

    <section class=\"card full\">
      <h2>Latest Incident Responses</h2>
    {_table_or_empty_html(['updated_at','severity','status','response_id','detail'], response_rows)}
    </section>

    <section class=\"card full\">
      <h2>Latest Incident Actions</h2>
    {_table_or_empty_html(['created_at','action_type','status','dry_run','detail'], action_rows)}
    </section>

    <section class=\"card full\">
      <h2>Latest Auto Recovery Executions</h2>
    {_table_or_empty_html(['created_at','action_type','status','manual_required','detail'], recovery_rows)}
    </section>

    <section class=\"card full\">
      <h2>Latest Notification Deliveries</h2>
      {_table_or_empty(['created_at','channel_id','status','alert_id'], delivery_rows)}
    </section>

        <section class=\"card full\">
            <h2>Latest Incident Timeline</h2>
            {_table_or_empty_html(['alert_created_at','severity','status','title','event_count','latest_event_at','latest_event_type','latest_event_status','alert_detail','timeline','filter_link','export','report'], timeline_rows_html)}
        </section>

    <section class=\"card full\">
      <h2>Artifacts</h2>
      {_table_or_empty(['artifact','missing','decode_error','error'], artifact_rows, empty_label='no artifacts')}
    </section>
  </div>
    {_render_copy_script()}
</body>
</html>
"""
    return html
