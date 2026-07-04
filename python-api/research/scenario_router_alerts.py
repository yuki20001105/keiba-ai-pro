from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _build_alert_candidates(
    *,
    run: dict[str, Any],
    max_fallback_rate: float,
    max_no_model_rate: float,
    min_roi_lift: float,
    min_hit_rate_lift: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    run_status = str(run.get("status") or run.get("run_status") or "")
    decision = str(run.get("decision") or "")
    action = str(run.get("action") or "")
    summary = (run.get("summary") or {}) if isinstance(run.get("summary"), dict) else {}

    fallback_rate = _safe_float(summary.get("fallback_rate"), 0.0)
    no_model_rate = _safe_float(summary.get("no_model_rate"), 0.0)
    roi_lift = _safe_float(summary.get("roi_lift"), 0.0)
    hit_rate_lift = _safe_float(summary.get("hit_rate_lift"), 0.0)

    if run_status.upper() == "FAILED":
        out.append(
            {
                "severity": "CRITICAL",
                "alert_type": "RUN_FAILED",
                "title": "Scenario Router Rollout Run Failed",
                "message": str(run.get("error_message") or "rollout scheduled run failed"),
            }
        )

    if decision == "STOP_CANARY":
        out.append(
            {
                "severity": "CRITICAL",
                "alert_type": "STOP_CANARY",
                "title": "Scenario Router Requested STOP_CANARY",
                "message": "decision=STOP_CANARY detected in rollout run",
            }
        )

    if decision == "ROLLBACK_TO_SHADOW":
        out.append(
            {
                "severity": "WARNING",
                "alert_type": "ROLLBACK_TO_SHADOW",
                "title": "Scenario Router Requested Rollback To Shadow",
                "message": "decision=ROLLBACK_TO_SHADOW detected in rollout run",
            }
        )

    if action == "STOP":
        out.append(
            {
                "severity": "CRITICAL",
                "alert_type": "ACTION_STOP",
                "title": "Scenario Router Rollout Action STOP",
                "message": "rollout action=STOP was executed",
            }
        )

    if action == "ROLLBACK":
        out.append(
            {
                "severity": "WARNING",
                "alert_type": "ACTION_ROLLBACK",
                "title": "Scenario Router Rollout Action ROLLBACK",
                "message": "rollout action=ROLLBACK was executed",
            }
        )

    if no_model_rate > float(max_no_model_rate):
        out.append(
            {
                "severity": "CRITICAL",
                "alert_type": "HIGH_NO_MODEL_RATE",
                "title": "Scenario Router no_model_rate Threshold Breach",
                "message": f"no_model_rate={no_model_rate:.4f} > max_no_model_rate={float(max_no_model_rate):.4f}",
            }
        )

    if fallback_rate > float(max_fallback_rate):
        out.append(
            {
                "severity": "WARNING",
                "alert_type": "HIGH_FALLBACK_RATE",
                "title": "Scenario Router fallback_rate Threshold Breach",
                "message": f"fallback_rate={fallback_rate:.4f} > max_fallback_rate={float(max_fallback_rate):.4f}",
            }
        )

    if roi_lift < float(min_roi_lift):
        out.append(
            {
                "severity": "WARNING",
                "alert_type": "LOW_ROI_LIFT",
                "title": "Scenario Router ROI Lift Degradation",
                "message": f"roi_lift={roi_lift:.4f} < min_roi_lift={float(min_roi_lift):.4f}",
            }
        )

    if hit_rate_lift < float(min_hit_rate_lift):
        out.append(
            {
                "severity": "WARNING",
                "alert_type": "LOW_HIT_RATE_LIFT",
                "title": "Scenario Router Hit-Rate Lift Degradation",
                "message": f"hit_rate_lift={hit_rate_lift:.4f} < min_hit_rate_lift={float(min_hit_rate_lift):.4f}",
            }
        )

    return out


def evaluate_scenario_router_alerts(
    *,
    mlops_db_path: str,
    target: str | None = None,
    source_run_id: str | None = None,
    max_fallback_rate: float = 0.50,
    max_no_model_rate: float = 0.05,
    min_roi_lift: float = -0.03,
    min_hit_rate_lift: float = -0.02,
    lookback_runs: int = 1,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")

    runs = db_store.list_router_rollout_runs(target=tgt, limit=max(1, int(lookback_runs)))
    if source_run_id:
        runs = [r for r in runs if str(r.get("run_id") or "") == str(source_run_id)]

    if not runs:
        return {
            "target": tgt,
            "processed_runs": 0,
            "created_alerts": 0,
            "deduped_alerts": 0,
            "alerts": [],
            "warnings": ["no rollout runs found for alert evaluation"],
        }

    created = 0
    deduped = 0
    out_alerts: list[dict[str, Any]] = []

    for run in runs:
        run_id = str(run.get("run_id") or "")
        decision = str(run.get("decision") or "")
        action = str(run.get("action") or "")
        summary = (run.get("summary") or {}) if isinstance(run.get("summary"), dict) else {}

        for cand in _build_alert_candidates(
            run=run,
            max_fallback_rate=float(max_fallback_rate),
            max_no_model_rate=float(max_no_model_rate),
            min_roi_lift=float(min_roi_lift),
            min_hit_rate_lift=float(min_hit_rate_lift),
        ):
            alert_type = str(cand.get("alert_type") or "")
            open_alert = db_store.get_open_router_alert(target=tgt, alert_type=alert_type)

            payload = {
                "run_id": run_id,
                "decision": decision,
                "action": action,
                "summary": summary,
            }

            if open_alert:
                db_store.add_router_alert_event(
                    alert_id=str(open_alert.get("alert_id") or ""),
                    event_type="TRIGGERED_AGAIN",
                    message=str(cand.get("message") or ""),
                    payload=payload,
                )
                deduped += 1
                out_alerts.append(
                    {
                        "alert_id": str(open_alert.get("alert_id") or ""),
                        "alert_type": alert_type,
                        "severity": str(open_alert.get("severity") or cand.get("severity") or ""),
                        "status": "open",
                        "deduped": True,
                    }
                )
                continue

            aid = db_store.create_router_alert(
                target=tgt,
                severity=str(cand.get("severity") or "WARNING"),
                alert_type=alert_type,
                title=str(cand.get("title") or alert_type),
                message=str(cand.get("message") or ""),
                source_run_id=run_id,
                decision=decision,
                action=action,
                summary=summary,
            )
            db_store.add_router_alert_event(
                alert_id=aid,
                event_type="CREATED",
                message=str(cand.get("message") or ""),
                payload=payload,
            )
            created += 1
            out_alerts.append(
                {
                    "alert_id": aid,
                    "alert_type": alert_type,
                    "severity": str(cand.get("severity") or ""),
                    "status": "open",
                    "deduped": False,
                }
            )

    return {
        "target": tgt,
        "processed_runs": int(len(runs)),
        "created_alerts": int(created),
        "deduped_alerts": int(deduped),
        "alerts": out_alerts,
        "warnings": [],
    }


def resolve_scenario_router_alert(
    *,
    mlops_db_path: str,
    alert_id: str,
    message: str = "",
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    ok = db_store.resolve_router_alert(alert_id=str(alert_id), message=str(message or ""))
    return {
        "success": bool(ok),
        "alert_id": str(alert_id),
    }
