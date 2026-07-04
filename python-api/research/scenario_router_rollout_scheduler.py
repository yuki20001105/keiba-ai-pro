from __future__ import annotations

from pathlib import Path
from typing import Any

from mlops import MLOpsStore
from research.scenario_router_rollout import (
    apply_scenario_router_rollout,
    evaluate_scenario_router_rollout,
    get_scenario_router_rollout_status,
)


def run_scenario_router_rollout_scheduled(
    *,
    mlops_db_path: str,
    race_db_path: str,
    target: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    stake_per_race: int = 100,
    min_races: int = 30,
    max_fallback_rate: float = 0.50,
    max_no_model_rate: float = 0.05,
    min_roi_lift: float = -0.03,
    min_hit_rate_lift: float = -0.02,
    rollout_steps: list[int] | None = None,
    apply_updates: bool = False,
    store: MLOpsStore | None = None,
) -> dict[str, Any]:
    db_store = store or MLOpsStore(db_path=Path(mlops_db_path))
    tgt = str(target or "win")

    # Ensure rollout state exists for target.
    current = get_scenario_router_rollout_status(
        mlops_db_path=mlops_db_path,
        target=tgt,
        create_if_missing=True,
        store=db_store,
    )

    try:
        if bool(apply_updates):
            data = apply_scenario_router_rollout(
                mlops_db_path=mlops_db_path,
                race_db_path=race_db_path,
                target=tgt,
                date_from=date_from,
                date_to=date_to,
                min_races=int(min_races),
                stake_per_race=int(stake_per_race),
                max_fallback_rate=float(max_fallback_rate),
                max_no_model_rate=float(max_no_model_rate),
                min_roi_lift=float(min_roi_lift),
                min_hit_rate_lift=float(min_hit_rate_lift),
                rollout_steps=rollout_steps,
                apply_updates=True,
                store=db_store,
            )
        else:
            data = evaluate_scenario_router_rollout(
                mlops_db_path=mlops_db_path,
                race_db_path=race_db_path,
                target=tgt,
                date_from=date_from,
                date_to=date_to,
                min_races=int(min_races),
                stake_per_race=int(stake_per_race),
                max_fallback_rate=float(max_fallback_rate),
                max_no_model_rate=float(max_no_model_rate),
                min_roi_lift=float(min_roi_lift),
                min_hit_rate_lift=float(min_hit_rate_lift),
                rollout_steps=rollout_steps,
                create_rollout_if_missing=False,
                store=db_store,
            )
            data = {
                **data,
                "applied": False,
                "event_saved": False,
                "warnings": ["scheduled run executed in dry-run mode"],
            }

        plan = dict(data.get("plan") or {})
        summary = dict(((data.get("evaluation") or {}).get("summary") or {}))

        run_id = db_store.insert_router_rollout_run(
            target=tgt,
            date_from=date_from,
            date_to=date_to,
            decision=str(plan.get("decision") or ""),
            action=str(plan.get("action") or ""),
            from_percent=int(plan.get("from_percent") or int(current.get("current_percent") or 0)),
            to_percent=int(plan.get("to_percent") or int(current.get("current_percent") or 0)),
            apply_updates=bool(apply_updates),
            status="SUCCESS",
            error_message=None,
            summary=summary,
        )

        return {
            **data,
            "run_id": str(run_id),
            "run_status": "SUCCESS",
        }
    except Exception as e:
        err_msg = str(e)
        run_id = db_store.insert_router_rollout_run(
            target=tgt,
            date_from=date_from,
            date_to=date_to,
            decision="",
            action="",
            from_percent=int(current.get("current_percent") or 0),
            to_percent=int(current.get("current_percent") or 0),
            apply_updates=bool(apply_updates),
            status="FAILED",
            error_message=err_msg,
            summary={},
        )
        return {
            "target": tgt,
            "current": current,
            "run_id": str(run_id),
            "run_status": "FAILED",
            "error_message": err_msg,
            "applied": False,
            "event_saved": False,
        }
