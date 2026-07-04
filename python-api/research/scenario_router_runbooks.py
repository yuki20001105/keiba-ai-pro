from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _extract_observed_metrics(alert: dict[str, Any]) -> dict[str, Any]:
    summary = alert.get("summary") if isinstance(alert.get("summary"), dict) else {}
    keys = [
        "roi_lift",
        "hit_rate_lift",
        "fallback_rate",
        "no_model_rate",
        "canary_active_races",
        "shadow_races",
    ]
    out: dict[str, Any] = {}
    for k in keys:
        if k in summary:
            out[k] = summary.get(k)
    return out


def _thresholds_for_alert(alert: dict[str, Any], resolved_type: str) -> list[dict[str, Any]]:
    # These are aligned with current alert-manager defaults unless explicitly overridden there.
    defaults: list[dict[str, Any]] = []
    if resolved_type in {"STOP_CANARY", "ROLLBACK_TO_SHADOW", "HIGH_NO_MODEL_RATE"}:
        defaults.append({"metric": "no_model_rate", "threshold": 0.05, "direction": "max"})
    if resolved_type in {"STOP_CANARY", "ROLLBACK_TO_SHADOW", "HIGH_FALLBACK_RATE"}:
        defaults.append({"metric": "fallback_rate", "threshold": 0.50, "direction": "max"})
    if resolved_type in {"STOP_CANARY", "ROLLBACK_TO_SHADOW", "ROI_DEGRADATION"}:
        defaults.append({"metric": "roi_lift", "threshold": -0.03, "direction": "min"})
    if resolved_type in {"STOP_CANARY", "ROLLBACK_TO_SHADOW", "HIT_RATE_DEGRADATION"}:
        defaults.append({"metric": "hit_rate_lift", "threshold": -0.02, "direction": "min"})
    if not defaults:
        defaults = [
            {"metric": "no_model_rate", "threshold": 0.05, "direction": "max"},
            {"metric": "fallback_rate", "threshold": 0.50, "direction": "max"},
            {"metric": "roi_lift", "threshold": -0.03, "direction": "min"},
            {"metric": "hit_rate_lift", "threshold": -0.02, "direction": "min"},
        ]
    return defaults


def _build_threshold_comparison(
    *,
    observed_metrics: dict[str, Any],
    thresholds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in thresholds:
        metric = str(t.get("metric") or "")
        direction = str(t.get("direction") or "max")
        threshold = _safe_float(t.get("threshold"))
        observed = _safe_float(observed_metrics.get(metric))
        if not metric or threshold is None:
            continue

        if observed is None:
            out.append(
                {
                    "metric": metric,
                    "observed": None,
                    "threshold": threshold,
                    "diff": None,
                    "status": "unknown",
                }
            )
            continue

        diff = float(observed - threshold)
        if direction == "min":
            status = "ok" if observed >= threshold else "breach"
        else:
            status = "ok" if observed <= threshold else "breach"

        out.append(
            {
                "metric": metric,
                "observed": observed,
                "threshold": threshold,
                "diff": diff,
                "status": status,
            }
        )
    return out


def _default_related_apis() -> list[str]:
    return [
        "GET /api/mlops/research/scenario-router/rollout/status",
        "GET /api/mlops/research/scenario-router/rollout/events",
        "GET /api/mlops/research/scenario-router/alerts",
        "POST /api/mlops/research/scenario-router/backtest",
        "POST /api/mlops/research/scenario-router/canary/evaluate",
    ]


def _build_template(alert: dict[str, Any]) -> dict[str, Any]:
    alert_type = str(alert.get("alert_type") or "").upper()
    decision = str(alert.get("decision") or "").upper()
    action = str(alert.get("action") or "").upper()

    resolved_type = alert_type
    if resolved_type == "ACTION_STOP":
        resolved_type = "STOP_CANARY"
    elif resolved_type == "ACTION_ROLLBACK":
        resolved_type = "ROLLBACK_TO_SHADOW"
    elif resolved_type == "LOW_ROI_LIFT":
        resolved_type = "ROI_DEGRADATION"
    elif resolved_type == "LOW_HIT_RATE_LIFT":
        resolved_type = "HIT_RATE_DEGRADATION"

    if decision == "STOP_CANARY":
        resolved_type = "STOP_CANARY"
    elif decision == "ROLLBACK_TO_SHADOW":
        resolved_type = "ROLLBACK_TO_SHADOW"
    elif action == "STOP":
        resolved_type = "STOP_CANARY"
    elif action == "ROLLBACK":
        resolved_type = "ROLLBACK_TO_SHADOW"

    base_title = str(alert.get("title") or "Scenario Router Incident")
    related_apis = _default_related_apis()

    if resolved_type in {"RUN_FAILED", "FAILED", "FAILED_RUN"}:
        return {
            "template": "FAILED_RUN",
            "title": f"Incident Runbook: FAILED run - {base_title}",
            "summary": "Scenario Router rollout scheduled run failed. Treat this as control-plane instability and stop further automatic promotion actions until root cause is clarified.",
            "root_cause_hypotheses": [
                "Scheduler/runtime execution error occurred during rollout job",
                "MLOps DB read/write path temporarily unavailable",
                "Canary evaluation failed due to missing/invalid recent prediction data",
                "Unexpected schema/config drift in rollout alert chain",
            ],
            "checklist": [
                "Confirm latest run status from rollout events and run history",
                "Inspect error_message from the failed run and related alert summary",
                "Verify mlops DB path and write permissions",
                "Run canary evaluate in dry-run mode and compare with prior successful run",
            ],
            "recommended_actions": [
                "Freeze automatic rollout progression until failure root cause is identified",
                "Re-run scheduler path with apply_updates=false to validate pipeline health",
                "Create/refresh incident note and attach run_id, alert_id, and error snippet",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "Consecutive dry-run execution succeeds without errors",
                "Canary metrics are available and within configured threshold",
                "No new RUN_FAILED alert is generated in the next scheduled cycle",
            ],
        }

    if resolved_type == "STOP_CANARY":
        return {
            "template": "STOP_CANARY",
            "title": "Incident Runbook: STOP_CANARY",
            "summary": "Canary evaluation triggered STOP_CANARY. Active expansion must remain halted until quality and fallback/no-model health recover.",
            "root_cause_hypotheses": [
                "no_model_rate exceeded threshold due to specialist coverage gap",
                "fallback_rate spiked due to routing mismatch or disabled policies",
                "router ROI/hit-rate lift degraded versus baseline/global model",
                "stale scenario_model_policies or priority inversion remained in production",
            ],
            "checklist": [
                "Verify rollout status is STOPPED or SHADOW_ONLY",
                "Confirm current_percent is reverted to safe state",
                "Check no_model_rate, fallback_rate, roi_lift, hit_rate_lift in latest summary",
                "Inspect active scenario_model_policies for stale or conflicting entries",
            ],
            "recommended_actions": [
                "Keep router in shadow until thresholds stabilize",
                "Disable or lower priority for problematic policy segments",
                "Re-run backtest and canary evaluate before any percent increase",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "no_model_rate <= configured max_no_model_rate",
                "fallback_rate <= configured max_fallback_rate",
                "router metrics no longer underperform baseline in shadow/canary checks",
                "E2E validation path passes for analyze_race and rollout APIs",
            ],
        }

    if resolved_type == "ROLLBACK_TO_SHADOW":
        return {
            "template": "ROLLBACK_TO_SHADOW",
            "title": "Incident Runbook: ROLLBACK_TO_SHADOW",
            "summary": "Rollout was rolled back to shadow mode. Router active exposure should remain reduced until performance and routing stability recover.",
            "root_cause_hypotheses": [
                "canary metrics regressed after percent increase",
                "route-level instability introduced by policy updates",
                "temporary data skew between canary and shadow cohorts",
            ],
            "checklist": [
                "Confirm rollout mode and percent after rollback",
                "Review latest rollout events around rollback decision",
                "Compare canary vs shadow by no_model_rate/fallback_rate/roi_lift/hit_rate_lift",
                "Check recently changed policy decisions and lifecycle actions",
            ],
            "recommended_actions": [
                "Stay in shadow and gather more stable canary sample before retry",
                "Re-tune policy thresholds or disable unstable scenario segment",
                "Re-run canary evaluate and only resume when all checks pass",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "canary metrics return within configured guardrails",
                "rollback trigger condition no longer reproduced in consecutive checks",
                "no new rollback/stop alerts in next scheduled evaluations",
            ],
        }

    if resolved_type == "HIGH_NO_MODEL_RATE":
        return {
            "template": "HIGH_NO_MODEL_RATE",
            "title": "Incident Runbook: HIGH_NO_MODEL_RATE",
            "summary": "No-model routing rate exceeded threshold. Router often cannot find suitable specialist model and may degrade to unsafe decisions.",
            "root_cause_hypotheses": [
                "specialist policy coverage insufficient for current scenario mix",
                "policy statuses disabled or stale for active target",
                "scenario key/value mismatch between router output and policy definitions",
            ],
            "checklist": [
                "Inspect no_model_rate trend in recent rollout summaries",
                "List active scenario policies and coverage across key segments",
                "Validate scenario keys produced by router for recent races",
            ],
            "recommended_actions": [
                "Add or reactivate policies for uncovered high-frequency scenarios",
                "Lower rollout percent while coverage is rebuilt",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "no_model_rate returns below configured threshold for consecutive runs",
                "coverage confirms active policies for major scenario segments",
            ],
        }

    if resolved_type == "HIGH_FALLBACK_RATE":
        return {
            "template": "HIGH_FALLBACK_RATE",
            "title": "Incident Runbook: HIGH_FALLBACK_RATE",
            "summary": "Fallback routing rate is high. Router decisions are frequently bypassed, reducing trust in specialist policy effectiveness.",
            "root_cause_hypotheses": [
                "policy matching precision is low for current scenario distribution",
                "selected models are unavailable or filtered out at runtime",
                "priority ordering causes frequent fallback to global model",
            ],
            "checklist": [
                "Check fallback_rate and route_type distribution",
                "Review policy priority and status for affected segments",
                "Validate selected_model_id presence in recent runs",
            ],
            "recommended_actions": [
                "adjust policy priority and status for stable specialist routes",
                "reduce active rollout percent until fallback stabilizes",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "fallback_rate remains below threshold across consecutive runs",
                "specialist route usage recovers without ROI/hit-rate degradation",
            ],
        }

    if resolved_type == "ROI_DEGRADATION":
        return {
            "template": "ROI_DEGRADATION",
            "title": "Incident Runbook: ROI_DEGRADATION",
            "summary": "Router ROI lift degraded below minimum guardrail. Continue rollout only after validating that strategy value has recovered.",
            "root_cause_hypotheses": [
                "segment-level policy effect drifted versus recent race conditions",
                "canary sample imbalance caused unstable ROI estimate",
                "specialist model quality decayed versus current baseline model",
            ],
            "checklist": [
                "Compare router ROI and global ROI in latest summary",
                "Inspect scenario breakdown for negative ROI-lift segments",
                "Re-run backtest over recent date window",
            ],
            "recommended_actions": [
                "pause expansion and collect additional shadow/canary evidence",
                "disable or downgrade policies with persistent negative ROI lift",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "roi_lift >= configured minimum for consecutive evaluations",
                "negative-lift segments have mitigation plan or disabled policy",
            ],
        }

    if resolved_type == "HIT_RATE_DEGRADATION":
        return {
            "template": "HIT_RATE_DEGRADATION",
            "title": "Incident Runbook: HIT_RATE_DEGRADATION",
            "summary": "Router hit-rate lift degraded below minimum guardrail. Treat as quality regression and hold expansion.",
            "root_cause_hypotheses": [
                "route selection drift weakened top-hit quality",
                "policy/model mismatch under current scenario distribution",
                "insufficient canary sample produced noisy lift estimate",
            ],
            "checklist": [
                "verify hit_rate_lift trend and recent confidence interval",
                "compare affected scenarios against baseline outcomes",
                "check if fallback/no-model spikes coincide with hit-rate drop",
            ],
            "recommended_actions": [
                "keep or return rollout to shadow while investigating",
                "apply policy fixes and validate in canary before re-expansion",
            ],
            "related_apis": related_apis,
            "recovery_conditions": [
                "hit_rate_lift >= configured minimum on repeated runs",
                "related fallback/no-model metrics are also stable",
            ],
        }

    return {
        "template": "GENERIC",
        "title": f"Incident Runbook: {base_title}",
        "summary": "Scenario Router alert requires operator validation before further rollout changes.",
        "root_cause_hypotheses": [
            "metric guardrail breach in recent rollout evaluation",
            "policy-state mismatch with current scenario distribution",
        ],
        "checklist": [
            "confirm current rollout status and latest events",
            "review alert summary and linked run metrics",
        ],
        "recommended_actions": [
            "hold rollout changes until findings are documented",
        ],
        "related_apis": related_apis,
        "recovery_conditions": [
            "guardrail metrics return to acceptable range",
        ],
    }


def _build_notification_summary(runbook: dict[str, Any]) -> str:
    checklist = runbook.get("checklist") if isinstance(runbook.get("checklist"), list) else []
    apis = runbook.get("related_apis") if isinstance(runbook.get("related_apis"), list) else []
    recovery = runbook.get("recovery_conditions") if isinstance(runbook.get("recovery_conditions"), list) else []
    threshold_cmp = runbook.get("threshold_comparison") if isinstance(runbook.get("threshold_comparison"), list) else []

    lines: list[str] = []
    lines.append(f"Runbook: {str(runbook.get('title') or '')}")
    lines.append(f"Summary: {str(runbook.get('summary') or '')}")
    if threshold_cmp:
        first = threshold_cmp[0]
        lines.append(
            "Metrics: "
            + f"{str(first.get('metric') or '')} observed={first.get('observed')} threshold={first.get('threshold')} diff={first.get('diff')} ({first.get('status')})"
        )
    if checklist:
        lines.append("Initial Actions: " + " | ".join([str(x) for x in checklist[:3]]))
    if apis:
        lines.append("Check APIs: " + " | ".join([str(x) for x in apis[:3]]))
    if recovery:
        lines.append("Recovery Conditions: " + " | ".join([str(x) for x in recovery[:3]]))
    return "\n".join(lines)


def generate_scenario_router_runbook(
    *,
    mlops_db_path: str,
    alert_id: str,
    include_notification_summary: bool = True,
    save_runbook: bool = True,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    aid = str(alert_id or "").strip()
    if not aid:
        raise ValueError("alert_id is required")

    alert = db_store.get_router_alert_by_id(alert_id=aid)
    if not alert:
        raise ValueError(f"alert_id not found: {aid}")

    tpl = _build_template(alert)
    observed_metrics = _extract_observed_metrics(alert)
    thresholds = _thresholds_for_alert(alert, str(tpl.get("template") or "GENERIC"))
    threshold_comparison = _build_threshold_comparison(
        observed_metrics=observed_metrics,
        thresholds=thresholds,
    )

    runbook = {
        "runbook_id": "",
        "alert_id": str(alert.get("alert_id") or ""),
        "target": str(alert.get("target") or ""),
        "severity": str(alert.get("severity") or "WARNING"),
        "alert_type": str(alert.get("alert_type") or ""),
        "title": str(tpl.get("title") or "Incident Runbook"),
        "summary": str(tpl.get("summary") or ""),
        "root_cause_hypotheses": list(tpl.get("root_cause_hypotheses") or []),
        "checklist": list(tpl.get("checklist") or []),
        "recommended_actions": list(tpl.get("recommended_actions") or []),
        "related_apis": list(tpl.get("related_apis") or []),
        "recovery_conditions": list(tpl.get("recovery_conditions") or []),
        "observed_metrics": observed_metrics,
        "threshold_comparison": threshold_comparison,
        "template": str(tpl.get("template") or "GENERIC"),
    }

    if bool(save_runbook):
        rid = db_store.insert_router_runbook(
            alert_id=runbook["alert_id"],
            target=runbook["target"],
            severity=runbook["severity"],
            alert_type=runbook["alert_type"],
            title=runbook["title"],
            summary=runbook["summary"],
            root_cause_hypotheses=runbook["root_cause_hypotheses"],
            checklist=runbook["checklist"],
            recommended_actions=runbook["recommended_actions"],
            related_apis=runbook["related_apis"],
            recovery_conditions=runbook["recovery_conditions"],
            observed_metrics=runbook["observed_metrics"],
            threshold_comparison=runbook["threshold_comparison"],
        )
        runbook["runbook_id"] = rid

    runbook["notification_summary"] = (
        _build_notification_summary(runbook)
        if bool(include_notification_summary)
        else ""
    )
    runbook["saved"] = bool(save_runbook)
    return runbook
