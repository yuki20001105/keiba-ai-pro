from __future__ import annotations

from typing import Any

from .experiment_registry import ExperimentOpsStore
from .knowledge_base import ResearchKnowledgeBase


def _family(feature_name: str) -> str:
    s = str(feature_name or "").lower()
    if any(k in s for k in ["odds", "popular"]):
        return "market"
    if any(k in s for k in ["weight", "burden"]):
        return "weight"
    if any(k in s for k in ["speed", "pace", "time"]):
        return "speed"
    if any(k in s for k in ["jockey", "trainer"]):
        return "human"
    if any(k in s for k in ["distance", "surface", "field", "condition"]):
        return "course"
    return "other"


def analyze_job_result(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    stages = result.get("stages") if isinstance(result.get("stages"), dict) else {}

    insights: list[dict[str, Any]] = []

    bt = stages.get("backtest") if isinstance(stages.get("backtest"), dict) else {}
    for row in (bt.get("by_model") or []):
        if not isinstance(row, dict):
            continue
        roi = float(row.get("roi") or 0.0)
        if roi > 0.0:
            insights.append(
                {
                    "signal_type": "model_positive_roi",
                    "metric": "roi",
                    "lift": roi,
                    "confidence": 0.6,
                    "feature_family": "model",
                    "condition_label": str(row.get("model_id") or ""),
                    "evidence": row,
                }
            )

    impact = stages.get("feature_impact") if isinstance(stages.get("feature_impact"), dict) else {}
    by_condition = impact.get("by_condition") if isinstance(impact.get("by_condition"), dict) else {}
    for cond_key, items in by_condition.items():
        if not isinstance(items, list):
            continue
        for row in items[:3]:
            if not isinstance(row, dict):
                continue
            lift = float(row.get("roi_lift_vs_all") or 0.0)
            n = int(row.get("n") or 0)
            if lift <= 0.0:
                continue
            conf = min(0.95, 0.4 + min(0.5, n / 500.0))
            insights.append(
                {
                    "signal_type": "condition_lift",
                    "metric": "roi",
                    "lift": lift,
                    "confidence": conf,
                    "feature_family": "condition",
                    "condition_label": f"{cond_key}:{row.get(cond_key)}",
                    "evidence": row,
                }
            )

    fi = impact.get("feature_impact") if isinstance(impact.get("feature_impact"), list) else []
    for row in fi[:10]:
        if not isinstance(row, dict):
            continue
        best = row.get("best_bucket") if isinstance(row.get("best_bucket"), dict) else {}
        lift = float(best.get("roi_lift_vs_all") or 0.0)
        n = int(best.get("n") or 0)
        if lift <= 0.0:
            continue
        f = str(row.get("feature") or "")
        conf = min(0.95, 0.45 + min(0.45, n / 600.0))
        insights.append(
            {
                "signal_type": "feature_bucket_lift",
                "metric": "roi",
                "lift": lift,
                "confidence": conf,
                "feature_family": _family(f),
                "condition_label": f"{f}:{best.get('bucket')}",
                "evidence": {"feature": f, "best_bucket": best},
            }
        )

    lab = stages.get("experiment_lab") if isinstance(stages.get("experiment_lab"), dict) else {}
    for cmp_row in (lab.get("comparisons") or []):
        if not isinstance(cmp_row, dict):
            continue
        ov = cmp_row.get("overlap_test") if isinstance(cmp_row.get("overlap_test"), dict) else {}
        if not bool(ov.get("significant_improvement")):
            continue
        delta = cmp_row.get("delta") if isinstance(cmp_row.get("delta"), dict) else {}
        lift = float(delta.get("roi") or 0.0)
        p = float(((ov.get("permutation") or {}).get("p_value") or 1.0))
        conf = max(0.5, min(0.98, 1.0 - p))
        insights.append(
            {
                "signal_type": "significant_model_improvement",
                "metric": "roi",
                "lift": lift,
                "confidence": conf,
                "feature_family": "model",
                "condition_label": str(cmp_row.get("challenger_model_id") or ""),
                "evidence": cmp_row,
            }
        )

    if not insights:
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        insights.append(
            {
                "signal_type": "run_observation",
                "metric": "roi",
                "lift": 0.0,
                "confidence": 0.2,
                "feature_family": "other",
                "condition_label": "insufficient_evidence",
                "evidence": {
                    "reason": "no positive lift signals detected",
                    "summary": summary,
                },
            }
        )

    insights.sort(key=lambda x: (float(x.get("confidence") or 0.0), float(x.get("lift") or 0.0)), reverse=True)
    return {
        "job_id": int(job.get("job_id") or 0),
        "name": str(job.get("name") or ""),
        "status": str(job.get("status") or ""),
        "insight_count": int(len(insights)),
        "insights": insights,
    }


def analyze_and_store_job(
    *,
    job_id: int,
    ops_store: ExperimentOpsStore | None = None,
    kb: ResearchKnowledgeBase | None = None,
) -> dict[str, Any]:
    ops = ops_store or ExperimentOpsStore()
    knowledge = kb or ResearchKnowledgeBase()
    job = ops.get_job(job_id=int(job_id))
    if not job:
        return {"success": False, "message": f"job not found: {job_id}"}

    analyzed = analyze_job_result(job)
    stored = 0
    for s in analyzed.get("insights") or []:
        if not isinstance(s, dict):
            continue
        knowledge.add_signal(
            source_job_id=int(job_id),
            signal_type=str(s.get("signal_type") or ""),
            metric=str(s.get("metric") or "roi"),
            lift=float(s.get("lift") or 0.0),
            confidence=float(s.get("confidence") or 0.0),
            feature_family=str(s.get("feature_family") or ""),
            condition_label=str(s.get("condition_label") or ""),
            evidence=(s.get("evidence") if isinstance(s.get("evidence"), dict) else {}),
        )
        stored += 1

    return {
        "success": True,
        "job_id": int(job_id),
        "insight_count": int(analyzed.get("insight_count") or 0),
        "stored_signals": int(stored),
        "top_insights": (analyzed.get("insights") or [])[:10],
    }
