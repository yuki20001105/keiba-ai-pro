from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse

from app_config import ULTIMATE_DB  # type: ignore
from betting.engine import optimize_kelly_portfolio  # type: ignore
from deps.auth import require_admin  # type: ignore
from knowledge.pace_model import analyze_race_pace, rebuild_pace_profiles  # type: ignore
from knowledge.scenario_engine import get_race_scenario, rebuild_race_scenarios  # type: ignore
from knowledge.track_bias import analyze_track_bias, rebuild_track_bias_profiles  # type: ignore
from mlops.backtest import run_backtest  # type: ignore
from mlops import MLOpsStore  # type: ignore
from models import AnalyzeRaceRequest, ScenarioAdoptionEvaluateRequest, ScenarioRouterBacktestRequest, ScenarioRouterPolicyOptimizeRequest, ScenarioPolicyLifecycleRequest, ScenarioRouterCanaryEvaluateRequest, ScenarioRouterRolloutEvaluateRequest, ScenarioRouterRolloutApplyRequest, ScenarioRouterRolloutScheduleRunRequest, ScenarioRouterAlertEvaluateRequest, ScenarioRouterAlertResolveRequest, ScenarioRouterNotificationDispatchRequest, ScenarioRouterNotificationTestRequest, ScenarioRouterRunbookGenerateRequest, IncidentActionPreviewRequest, IncidentActionExecuteRequest, IncidentResponsePrepareRequest, AutoRecoveryEvaluateRequest, AutoRecoveryExecuteRequest  # type: ignore
from research.experiment_analyzer import analyze_and_store_job  # type: ignore
from research.scenario_adoption_gate import evaluate_scenario_adoption  # type: ignore
from research.scenario_router_backtest import run_scenario_router_backtest  # type: ignore
from research.scenario_router_canary import evaluate_scenario_router_canary  # type: ignore
from research.scenario_router_rollout import evaluate_scenario_router_rollout, apply_scenario_router_rollout, get_scenario_router_rollout_status  # type: ignore
from research.scenario_router_rollout_scheduler import run_scenario_router_rollout_scheduled  # type: ignore
from research.scenario_router_alerts import evaluate_scenario_router_alerts, resolve_scenario_router_alert  # type: ignore
from research.scenario_router_notifications import dispatch_scenario_router_notifications, test_scenario_router_notification_channel  # type: ignore
from research.scenario_router_runbooks import generate_scenario_router_runbook  # type: ignore
from research.scenario_router_incident_actions import preview_scenario_router_incident_actions, execute_scenario_router_incident_action  # type: ignore
from research.scenario_router_incident_response import prepare_scenario_router_incident_response  # type: ignore
from research.scenario_router_auto_recovery import evaluate_scenario_router_auto_recovery, execute_scenario_router_auto_recovery  # type: ignore
from research.scenario_router_ops_dashboard import get_scenario_router_ops_dashboard, get_scenario_router_ops_audit_latest, get_scenario_router_ops_audit_history, get_scenario_router_ops_incidents_latest, get_scenario_router_ops_timeline, get_scenario_router_incident_timeline, build_scenario_router_ops_timeline_export, build_scenario_router_incident_timeline_export, build_scenario_router_ops_timeline_report, build_scenario_router_incident_timeline_report, render_scenario_router_ops_dashboard_html, render_scenario_router_ops_timeline_html, render_ops_alert_detail_html, render_ops_runbook_detail_html, render_ops_response_detail_html, render_ops_action_detail_html, render_ops_auto_recovery_execution_detail_html, render_ops_notification_delivery_detail_html  # type: ignore
from research.scenario_policy_lifecycle import apply_scenario_policy_lifecycle  # type: ignore
from research.scenario_router_policy_optimizer import optimize_scenario_router_policies  # type: ignore
from research.scenario_model_router import resolve_scenario_model  # type: ignore
from research.experiment_generator import generate_experiment_specs  # type: ignore
from research.experiment_lab import run_experiment_lab  # type: ignore
from research.experiment_planner import plan_experiments_from_goal  # type: ignore
from research.experiment_queue import submit_experiment_yaml  # type: ignore
from research.experiment_recommender import recommend_next_experiments  # type: ignore
from research.experiment_registry import ExperimentOpsStore  # type: ignore
from research.experiment_scheduler import run_next_experiment_job  # type: ignore
from research.feature_impact import run_feature_impact_analysis  # type: ignore
from research.knowledge_base import ResearchKnowledgeBase  # type: ignore
from routers.predict import analyze_race  # type: ignore

router = APIRouter(prefix="/api/mlops", tags=["mlops"])


def _q_int(value: str | None, *, default: int, min_v: int, max_v: int) -> int:
    try:
        n = int(str(value or "").strip())
    except Exception:
        n = int(default)
    if n < min_v:
        return int(default)
    return max(min_v, min(max_v, n))


def _q_refresh(value: str | None, *, default: int = 0, max_v: int = 3600) -> int:
    try:
        n = int(str(value or "").strip())
    except Exception:
        return int(default)
    if n <= 0:
        return 0
    return max(0, min(max_v, n))


def _q_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return bool(default)


@router.get("/experiments")
async def list_experiments(limit: int = 50, _: dict = Depends(require_admin)):
    store = MLOpsStore()
    return {"items": store.list_experiments(limit=limit), "limit": max(1, min(int(limit), 200))}


@router.get("/models")
async def list_models(limit: int = 50, _: dict = Depends(require_admin)):
    store = MLOpsStore()
    return {"items": store.list_models(limit=limit), "limit": max(1, min(int(limit), 200))}


@router.post("/models/{model_id}/promote")
async def promote_model(model_id: str, target: str, notes: str = "", _: dict = Depends(require_admin)):
    store = MLOpsStore()
    changed = store.promote_model(model_id=model_id, target=target, notes=notes)
    if changed <= 0:
        raise HTTPException(status_code=404, detail=f"model_id not found in registry: {model_id}")
    return {"success": True, "model_id": model_id, "target": target, "promoted_rows": changed}


@router.post("/models/auto-promote")
async def auto_promote_model(
    target: str,
    min_auc: float = 0.84,
    min_feature_quality: float = 95.0,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    models = store.list_models(limit=200)
    candidates = [m for m in models if str(m.get("target")) == target and str(m.get("stage")) == "candidate"]
    eligible = []
    for m in candidates:
        metrics = m.get("metrics") or {}
        auc = float(metrics.get("auc") or metrics.get("cv_auc_mean") or 0.0)
        fqs = float(m.get("feature_quality_score") or 0.0)
        if auc >= float(min_auc) and fqs >= float(min_feature_quality):
            eligible.append((auc, fqs, m))
    if not eligible:
        return {
            "success": False,
            "message": "no eligible candidate",
            "target": target,
            "thresholds": {"min_auc": min_auc, "min_feature_quality": min_feature_quality},
        }
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen = eligible[0][2]
    changed = store.promote_model(
        model_id=str(chosen.get("model_id")),
        target=target,
        notes=(
            f"auto-promoted by gate: auc>={min_auc}, "
            f"feature_quality>={min_feature_quality}"
        ),
    )
    return {
        "success": changed > 0,
        "target": target,
        "model_id": chosen.get("model_id"),
        "promoted_rows": changed,
        "auc": float((chosen.get("metrics") or {}).get("auc") or (chosen.get("metrics") or {}).get("cv_auc_mean") or 0.0),
        "feature_quality_score": float(chosen.get("feature_quality_score") or 0.0),
    }


@router.get("/predictions")
async def list_predictions(limit: int = 50, _: dict = Depends(require_admin)):
    store = MLOpsStore()
    return {"items": store.list_predictions(limit=limit), "limit": max(1, min(int(limit), 300))}


@router.get("/predictions/{prediction_id}")
async def get_prediction(prediction_id: str, _: dict = Depends(require_admin)):
    store = MLOpsStore()
    data = store.get_prediction(prediction_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"prediction_id not found: {prediction_id}")
    return data


@router.post("/predictions/{prediction_id}/evaluate")
async def evaluate_prediction(prediction_id: str, _: dict = Depends(require_admin)):
    store = MLOpsStore()
    data = store.evaluate_prediction(prediction_id=prediction_id, race_db_path=str(ULTIMATE_DB))
    if not data:
        raise HTTPException(status_code=404, detail=f"prediction not evaluable: {prediction_id}")
    return {"success": True, "evaluation": data}


@router.post("/bets")
async def register_bet(
    race_id: str,
    bet_type: str,
    combinations: str,
    unit_price: int,
    quantity: int,
    total_cost: int,
    prediction_id: str | None = None,
    expected_return: float | None = None,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    combos = [s.strip() for s in combinations.split(",") if s.strip()]
    rid = store.record_bet(
        prediction_id=prediction_id,
        race_id=race_id,
        bet_type=bet_type,
        combinations=combos,
        unit_price=unit_price,
        quantity=quantity,
        total_cost=total_cost,
        expected_return=expected_return,
        odds=None,
        status="planned",
        payout=None,
    )
    return {"success": True, "bet_id": rid}


@router.post("/champion-challenger/compare")
async def compare_champion_challenger(
    race_id: str,
    champion_model_id: str,
    challenger_model_id: str,
    bankroll: int = 10000,
    risk_mode: str = "balanced",
    _: dict = Depends(require_admin),
):
    req_champion = AnalyzeRaceRequest(
        race_id=race_id,
        model_id=champion_model_id,
        bankroll=bankroll,
        risk_mode=risk_mode,
    )
    req_challenger = AnalyzeRaceRequest(
        race_id=race_id,
        model_id=challenger_model_id,
        bankroll=bankroll,
        risk_mode=risk_mode,
    )
    champion = await analyze_race(req_champion)
    challenger = await analyze_race(req_challenger)

    def _summary(resp: dict) -> dict:
        preds = resp.get("predictions") or []
        top3 = [p.get("horse_number") for p in preds[:3]]
        evs = [float(p.get("expected_value")) for p in preds if p.get("expected_value") is not None]
        avg_ev = (sum(evs) / len(evs)) if evs else 0.0
        return {
            "top3": top3,
            "best_bet_type": (resp.get("best_bet_type") or ""),
            "avg_expected_value": avg_ev,
            "race_level": (resp.get("race_level") or ""),
        }

    c = champion.dict()
    d = challenger.dict()
    csum = _summary(c)
    dsum = _summary(d)
    return {
        "race_id": race_id,
        "champion": {"model_id": champion_model_id, **csum},
        "challenger": {"model_id": challenger_model_id, **dsum},
        "delta": {
            "avg_expected_value": float(dsum.get("avg_expected_value", 0.0)) - float(csum.get("avg_expected_value", 0.0)),
            "top3_overlap": len(set(csum.get("top3") or []) & set(dsum.get("top3") or [])),
        },
    }


@router.post("/backtest/run")
async def run_prediction_backtest(
    model_ids: str = "",
    date_from: str = "",
    date_to: str = "",
    stake_per_race: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    model_list = [s.strip() for s in model_ids.split(",") if s.strip()]
    data = run_backtest(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        model_ids=(model_list or None),
        date_from=(date_from or None),
        date_to=(date_to or None),
        stake_per_race=int(stake_per_race),
    )
    return data


@router.post("/betting/optimize")
async def optimize_betting_for_prediction(
    prediction_id: str,
    bankroll: int = 10000,
    per_race_limit: int = 500,
    min_ev: float = 1.1,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    pred = store.get_prediction(prediction_id)
    if not pred:
        raise HTTPException(status_code=404, detail=f"prediction_id not found: {prediction_id}")
    rec = optimize_kelly_portfolio(
        predictions=pred.get("results") or [],
        bankroll=int(bankroll),
        per_race_limit=int(per_race_limit),
        min_ev=float(min_ev),
    )
    return {
        "prediction_id": prediction_id,
        "race_id": pred.get("race_id"),
        "portfolio": rec,
    }


@router.post("/feature-impact/analyze")
async def analyze_feature_impact(
    model_ids: str = "",
    date_from: str = "",
    date_to: str = "",
    stake_per_race: int = 100,
    feature_columns: str = "",
    max_predictions: int = 5000,
    min_group_size: int = 20,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    mids = [s.strip() for s in model_ids.split(",") if s.strip()]
    cols = [s.strip() for s in feature_columns.split(",") if s.strip()]
    data = run_feature_impact_analysis(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        model_ids=(mids or None),
        date_from=(date_from or None),
        date_to=(date_to or None),
        stake_per_race=int(stake_per_race),
        feature_columns=cols,
        max_predictions=int(max_predictions),
        min_group_size=int(min_group_size),
    )
    return data


@router.post("/research/experiment-lab/compare")
async def compare_experiment_lab(
    baseline_model_id: str,
    challenger_model_ids: str,
    date_from: str = "",
    date_to: str = "",
    stake_per_race: int = 100,
    max_predictions: int = 5000,
    bootstrap_iters: int = 3000,
    permutation_iters: int = 5000,
    scenario_segment_by: str = "expected_pace,expected_bias,winning_pattern",
    min_segment_overlap: int = 20,
    _: dict = Depends(require_admin),
):
    challengers = [s.strip() for s in challenger_model_ids.split(",") if s.strip()]
    segment_by = [s.strip() for s in scenario_segment_by.split(",") if s.strip()]
    data = run_experiment_lab(
        mlops_db_path=str(MLOpsStore().db_path),
        race_db_path=str(ULTIMATE_DB),
        baseline_model_id=baseline_model_id,
        challenger_model_ids=challengers,
        date_from=(date_from or None),
        date_to=(date_to or None),
        stake_per_race=int(stake_per_race),
        max_predictions=int(max_predictions),
        bootstrap_iters=int(bootstrap_iters),
        permutation_iters=int(permutation_iters),
        scenario_segment_by=segment_by,
        min_segment_overlap=int(min_segment_overlap),
    )
    return data


@router.post("/research/scenario-adoption/evaluate")
async def evaluate_scenario_adoption_gate(
    request: ScenarioAdoptionEvaluateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = evaluate_scenario_adoption(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        baseline_model_id=str(request.baseline_model_id),
        challenger_model_id=str(request.challenger_model_id),
        scenario_segment_by=list(request.scenario_segment_by or []),
        min_segment_overlap=int(request.min_segment_overlap),
        alpha=float(request.alpha),
        fdr_alpha=float(request.fdr_alpha),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        min_ev_lift=float(request.min_ev_lift),
        require_positive_ci_lower=bool(request.require_positive_ci_lower),
        max_allowed_global_roi_drop=float(request.max_allowed_global_roi_drop),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        stake_per_race=int(request.stake_per_race),
        max_predictions=int(request.max_predictions),
        bootstrap_iters=int(request.bootstrap_iters),
        permutation_iters=int(request.permutation_iters),
        save_decisions=bool(request.save_decisions),
        save_policies=bool(request.save_policies),
        experiment_id=(str(request.experiment_id) if request.experiment_id else None),
        store=store,
    )
    return data


@router.get("/research/scenario-router/resolve/{race_id}")
async def resolve_scenario_route(
    race_id: str,
    target: str = "",
    use_router: bool = True,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = resolve_scenario_model(
        race_id=race_id,
        race_db_path=str(ULTIMATE_DB),
        target=(target or None),
        use_router=bool(use_router),
        store=store,
    )
    return data


@router.post("/research/scenario-router/backtest")
async def backtest_scenario_router(
    request: ScenarioRouterBacktestRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = run_scenario_router_backtest(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        target=(str(request.target) if request.target else None),
        router_mode=str(request.router_mode),
        stake_per_race=int(request.stake_per_race),
        scenario_segment_by=list(request.scenario_segment_by or []),
        min_races=int(request.min_races),
        include_route_type_breakdown=bool(request.include_route_type_breakdown),
        include_scenario_breakdown=bool(request.include_scenario_breakdown),
    )
    return data


@router.post("/research/scenario-router/optimize-policies")
async def optimize_scenario_router_policy_actions(
    request: ScenarioRouterPolicyOptimizeRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = optimize_scenario_router_policies(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        target=(str(request.target) if request.target else None),
        stake_per_race=int(request.stake_per_race),
        scenario_segment_by=list(request.scenario_segment_by or []),
        min_races=int(request.min_races),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        disable_if_roi_lift_below=float(request.disable_if_roi_lift_below),
        disable_if_hit_rate_lift_below=float(request.disable_if_hit_rate_lift_below),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        priority_step=int(request.priority_step),
        apply_updates=bool(request.apply_updates),
        save_evaluations=bool(request.save_evaluations),
        store=store,
    )
    return data


@router.post("/research/scenario-router/apply-lifecycle")
async def apply_scenario_router_policy_lifecycle(
    request: ScenarioPolicyLifecycleRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = apply_scenario_policy_lifecycle(
        mlops_db_path=str(store.db_path),
        target=(str(request.target) if request.target else None),
        lookback_evaluations=int(request.lookback_evaluations),
        raise_confirmations=int(request.raise_confirmations),
        disable_confirmations=int(request.disable_confirmations),
        watch_to_lower_threshold=int(request.watch_to_lower_threshold),
        needs_more_data_to_watch_threshold=int(request.needs_more_data_to_watch_threshold),
        cooldown_days=int(request.cooldown_days),
        priority_step=int(request.priority_step),
        apply_updates=bool(request.apply_updates),
        store=store,
    )
    return data


@router.post("/research/scenario-router/canary/evaluate")
async def evaluate_scenario_router_canary_mode(
    request: ScenarioRouterCanaryEvaluateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = evaluate_scenario_router_canary(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        target=(str(request.target) if request.target else None),
        min_races=int(request.min_races),
        canary_percent=(int(request.canary_percent) if request.canary_percent is not None else None),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        stake_per_race=int(request.stake_per_race),
        store=store,
    )
    return data


@router.get("/research/scenario-router/rollout/status")
async def get_scenario_router_rollout_current_status(
    target: str = "win",
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = get_scenario_router_rollout_status(
        mlops_db_path=str(store.db_path),
        target=(target or "win"),
        create_if_missing=True,
        store=store,
    )
    return data


@router.post("/research/scenario-router/rollout/evaluate")
async def evaluate_scenario_router_rollout_controller(
    request: ScenarioRouterRolloutEvaluateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = evaluate_scenario_router_rollout(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        target=(str(request.target) if request.target else None),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        min_races=int(request.min_races),
        canary_percent=(int(request.canary_percent) if request.canary_percent is not None else None),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        stake_per_race=int(request.stake_per_race),
        rollout_steps=[int(x) for x in (request.rollout_steps or [])],
        create_rollout_if_missing=False,
        store=store,
    )
    return data


@router.post("/research/scenario-router/rollout/apply")
async def apply_scenario_router_rollout_controller(
    request: ScenarioRouterRolloutApplyRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = apply_scenario_router_rollout(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        target=(str(request.target) if request.target else None),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        min_races=int(request.min_races),
        canary_percent=(int(request.canary_percent) if request.canary_percent is not None else None),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        stake_per_race=int(request.stake_per_race),
        rollout_steps=[int(x) for x in (request.rollout_steps or [])],
        apply_updates=bool(request.apply_updates),
        store=store,
    )
    return data


@router.get("/research/scenario-router/rollout/events")
async def list_scenario_router_rollout_events(
    target: str = "win",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    rollout = get_scenario_router_rollout_status(
        mlops_db_path=str(store.db_path),
        target=(target or "win"),
        create_if_missing=True,
        store=store,
    )
    events = store.list_router_rollout_events(
        target=(target or "win"),
        rollout_id=str(rollout.get("rollout_id") or ""),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "rollout": rollout,
        "items": events,
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/scenario-router/rollout/run-scheduled")
async def run_scenario_router_rollout_scheduled_job(
    request: ScenarioRouterRolloutScheduleRunRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = run_scenario_router_rollout_scheduled(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        target=(str(request.target) if request.target else None),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        stake_per_race=int(request.stake_per_race),
        min_races=int(request.min_races),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        rollout_steps=[int(x) for x in (request.rollout_steps or [])],
        apply_updates=bool(request.apply_updates),
        store=store,
    )
    return data


@router.post("/research/scenario-router/alerts/evaluate")
async def evaluate_scenario_router_alert_manager(
    request: ScenarioRouterAlertEvaluateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = evaluate_scenario_router_alerts(
        mlops_db_path=str(store.db_path),
        target=(str(request.target) if request.target else None),
        source_run_id=(str(request.source_run_id) if request.source_run_id else None),
        max_fallback_rate=float(request.max_fallback_rate),
        max_no_model_rate=float(request.max_no_model_rate),
        min_roi_lift=float(request.min_roi_lift),
        min_hit_rate_lift=float(request.min_hit_rate_lift),
        lookback_runs=int(request.lookback_runs),
        store=store,
    )
    return data


@router.get("/research/scenario-router/alerts")
async def list_scenario_router_alerts(
    target: str = "win",
    status: str = "open",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_router_alerts(
        target=(target or "win"),
        status=(status or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "status": str(status or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/scenario-router/alerts/{alert_id}/resolve")
async def resolve_scenario_router_alert_api(
    alert_id: str,
    request: ScenarioRouterAlertResolveRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = resolve_scenario_router_alert(
        mlops_db_path=str(store.db_path),
        alert_id=str(alert_id),
        message=str(request.message or ""),
        store=store,
    )
    if not bool(data.get("success")):
        raise HTTPException(status_code=404, detail=f"alert_id not found or already resolved: {alert_id}")
    return data


@router.post("/research/scenario-router/notifications/dispatch")
async def dispatch_scenario_router_notifications_api(
    request: ScenarioRouterNotificationDispatchRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = dispatch_scenario_router_notifications(
        mlops_db_path=str(store.db_path),
        target=(str(request.target) if request.target else None),
        severity_min=str(request.severity_min),
        channel_types=[str(x) for x in (request.channel_types or [])],
        apply_send=bool(request.apply_send),
        limit=int(request.limit),
        store=store,
    )
    return data


@router.get("/research/scenario-router/notifications/deliveries")
async def list_scenario_router_notification_deliveries_api(
    target: str = "win",
    status: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_router_notification_deliveries(
        target=(target or "win"),
        status=(status or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "status": str(status or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/scenario-router/notifications/test")
async def test_scenario_router_notification_channel_api(
    request: ScenarioRouterNotificationTestRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = test_scenario_router_notification_channel(
            mlops_db_path=str(store.db_path),
            channel_type=str(request.channel_type),
            name=str(request.name),
            config=dict(request.config or {}),
            payload=dict(request.payload or {}),
            alert_id=(str(request.alert_id) if request.alert_id else None),
            include_runbook_summary=bool(request.include_runbook_summary),
            apply_send=bool(request.apply_send),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/research/scenario-router/runbooks/generate")
async def generate_scenario_router_runbook_api(
    request: ScenarioRouterRunbookGenerateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = generate_scenario_router_runbook(
            mlops_db_path=str(store.db_path),
            alert_id=str(request.alert_id),
            include_notification_summary=bool(request.include_notification_summary),
            save_runbook=bool(request.save_runbook),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get("/research/scenario-router/runbooks")
async def list_scenario_router_runbooks_api(
    target: str = "win",
    alert_id: str = "",
    severity: str = "",
    alert_type: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_router_runbooks(
        target=(target or "win"),
        alert_id=(alert_id or None),
        severity=(severity or None),
        alert_type=(alert_type or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "alert_id": str(alert_id or ""),
        "severity": str(severity or ""),
        "alert_type": str(alert_type or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.get("/research/scenario-router/runbooks/{runbook_id}")
async def get_scenario_router_runbook_api(
    runbook_id: str,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    item = store.get_router_runbook_by_id(runbook_id=str(runbook_id))
    if not item:
        raise HTTPException(status_code=404, detail=f"runbook_id not found: {runbook_id}")
    return item


@router.post("/research/scenario-router/incidents/actions/preview")
async def preview_scenario_router_incident_actions_api(
    request: IncidentActionPreviewRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = preview_scenario_router_incident_actions(
            mlops_db_path=str(store.db_path),
            alert_id=(str(request.alert_id) if request.alert_id else None),
            runbook_id=(str(request.runbook_id) if request.runbook_id else None),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/research/scenario-router/incidents/actions/execute")
async def execute_scenario_router_incident_action_api(
    request: IncidentActionExecuteRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    data = execute_scenario_router_incident_action(
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        action_type=str(request.action_type),
        alert_id=(str(request.alert_id) if request.alert_id else None),
        runbook_id=(str(request.runbook_id) if request.runbook_id else None),
        apply_updates=bool(request.apply_updates),
        confirm=bool(request.confirm),
        requested_by=str(request.requested_by or ""),
        approved_by=str(request.approved_by or ""),
        policy_id=(str(request.policy_id) if request.policy_id else None),
        priority_delta=int(request.priority_delta),
        date_from=(str(request.date_from) if request.date_from else None),
        date_to=(str(request.date_to) if request.date_to else None),
        stake_per_race=int(request.stake_per_race),
        min_races=int(request.min_races),
        store=store,
    )
    if not bool(data.get("success")):
        msg = str(data.get("error") or "incident action failed")
        if str(data.get("status") or "") == "SKIPPED":
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return data


@router.get("/research/scenario-router/incidents/actions")
async def list_scenario_router_incident_actions_api(
    target: str = "win",
    alert_id: str = "",
    runbook_id: str = "",
    action_type: str = "",
    status: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_incident_actions(
        target=(target or "win"),
        alert_id=(alert_id or None),
        runbook_id=(runbook_id or None),
        action_type=(action_type or None),
        status=(status or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "alert_id": str(alert_id or ""),
        "runbook_id": str(runbook_id or ""),
        "action_type": str(action_type or ""),
        "status": str(status or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/scenario-router/incidents/response/prepare")
async def prepare_scenario_router_incident_response_api(
    request: IncidentResponsePrepareRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = prepare_scenario_router_incident_response(
            mlops_db_path=str(store.db_path),
            alert_id=str(request.alert_id),
            save_response=bool(request.save_response),
            include_runbook_summary=bool(request.include_runbook_summary),
            notification_channel_type=str(request.notification_channel_type),
            include_action_preview=bool(request.include_action_preview),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get("/research/scenario-router/incidents/responses")
async def list_scenario_router_incident_responses_api(
    target: str = "win",
    alert_id: str = "",
    status: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_incident_responses(
        target=(target or "win"),
        alert_id=(alert_id or None),
        status=(status or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "target": str(target or "win"),
        "alert_id": str(alert_id or ""),
        "status": str(status or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.get("/research/scenario-router/incidents/responses/{response_id}")
async def get_scenario_router_incident_response_api(
    response_id: str,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    item = store.get_incident_response_by_id(response_id=str(response_id))
    if not item:
        raise HTTPException(status_code=404, detail=f"response_id not found: {response_id}")
    return item


@router.post("/research/scenario-router/auto-recovery/evaluate")
async def evaluate_scenario_router_auto_recovery_api(
    request: AutoRecoveryEvaluateRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = evaluate_scenario_router_auto_recovery(
            mlops_db_path=str(store.db_path),
            response_id=(str(request.response_id) if request.response_id else None),
            alert_id=(str(request.alert_id) if request.alert_id else None),
            include_action_preview=bool(request.include_action_preview),
            include_runbook_summary=bool(request.include_runbook_summary),
            notification_channel_type=str(request.notification_channel_type),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.post("/research/scenario-router/auto-recovery/execute")
async def execute_scenario_router_auto_recovery_api(
    request: AutoRecoveryExecuteRequest,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    try:
        data = execute_scenario_router_auto_recovery(
            mlops_db_path=str(store.db_path),
            race_db_path=str(ULTIMATE_DB),
            response_id=(str(request.response_id) if request.response_id else None),
            alert_id=(str(request.alert_id) if request.alert_id else None),
            apply_updates=bool(request.apply_updates),
            confirm=bool(request.confirm),
            requested_by=str(request.requested_by or ""),
            approved_by=str(request.approved_by or ""),
            include_action_preview=bool(request.include_action_preview),
            include_runbook_summary=bool(request.include_runbook_summary),
            notification_channel_type=str(request.notification_channel_type),
            store=store,
        )
        return data
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)


@router.get("/research/scenario-router/auto-recovery/executions")
async def list_scenario_router_auto_recovery_executions_api(
    response_id: str = "",
    alert_id: str = "",
    action_type: str = "",
    status: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    store = MLOpsStore()
    items = store.list_auto_recovery_executions(
        response_id=(response_id or None),
        alert_id=(alert_id or None),
        action_type=(action_type or None),
        status=(status or None),
        limit=max(1, min(int(limit), 500)),
    )
    return {
        "response_id": str(response_id or ""),
        "alert_id": str(alert_id or ""),
        "action_type": str(action_type or ""),
        "status": str(status or ""),
        "items": items,
        "limit": max(1, min(int(limit), 500)),
    }


@router.get("/research/scenario-router/ops/dashboard")
async def get_scenario_router_ops_dashboard_api(
    target: str = "win",
    limit: str = "10",
    refresh: str = "0",
    show_raw_links: str = "false",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=10, min_v=1, max_v=100)
    rsec = _q_refresh(refresh, default=0, max_v=3600)
    show_links = _q_bool(show_raw_links, default=False)
    data = get_scenario_router_ops_dashboard(
        target=(target or "win"),
        limit=n,
    )
    links = {
        "dashboard": f"/api/mlops/research/scenario-router/ops/dashboard?target={target or 'win'}&limit={n}&refresh={rsec}&show_raw_links={str(show_links).lower()}",
        "audit_latest": "/api/mlops/research/scenario-router/ops/audit/latest",
        "audit_history": f"/api/mlops/research/scenario-router/ops/audit/history?limit={n}",
        "incidents_latest": f"/api/mlops/research/scenario-router/ops/incidents/latest?target={target or 'win'}&limit={n}",
        "timeline": f"/api/mlops/research/scenario-router/ops/timeline?target={target or 'win'}&limit={n}",
    }
    data["applied_filters"] = {
        **(data.get("applied_filters") if isinstance(data.get("applied_filters"), dict) else {}),
        "target": str(target or "win"),
        "limit": n,
        "refresh": rsec,
        "show_raw_links": show_links,
    }
    if show_links:
        data["links"] = links
    return data


@router.get("/research/scenario-router/ops/dashboard.html", response_class=HTMLResponse)
async def get_scenario_router_ops_dashboard_html_api(
    target: str = "win",
    limit: str = "10",
    refresh: str = "0",
    show_raw_links: str = "false",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=10, min_v=1, max_v=100)
    rsec = _q_refresh(refresh, default=0, max_v=3600)
    show_links = _q_bool(show_raw_links, default=False)
    html = render_scenario_router_ops_dashboard_html(
        target=(target or "win"),
        limit=n,
        refresh_sec=rsec,
        show_raw_links=show_links,
    )
    return HTMLResponse(content=html, status_code=200)


@router.get("/research/scenario-router/ops/alerts/{alert_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_alert_detail_html_api(
    alert_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_alert_detail_html(alert_id=str(alert_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/runbooks/{runbook_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_runbook_detail_html_api(
    runbook_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_runbook_detail_html(runbook_id=str(runbook_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/responses/{response_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_response_detail_html_api(
    response_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_response_detail_html(response_id=str(response_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/actions/{action_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_action_detail_html_api(
    action_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_action_detail_html(action_id=str(action_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/auto-recovery/executions/{execution_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_auto_recovery_execution_detail_html_api(
    execution_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_auto_recovery_execution_detail_html(execution_id=str(execution_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/notification-deliveries/{delivery_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_notification_delivery_detail_html_api(
    delivery_id: str,
    _: dict = Depends(require_admin),
):
    html, status = render_ops_notification_delivery_detail_html(delivery_id=str(delivery_id))
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/audit/latest")
async def get_scenario_router_ops_audit_latest_api(
    _: dict = Depends(require_admin),
):
    return get_scenario_router_ops_audit_latest()


@router.get("/research/scenario-router/ops/audit/history")
async def get_scenario_router_ops_audit_history_api(
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    return get_scenario_router_ops_audit_history(limit=max(1, min(int(limit), 500)))


@router.get("/research/scenario-router/ops/incidents/latest")
async def get_scenario_router_ops_incidents_latest_api(
    target: str = "win",
    limit: int = 10,
    _: dict = Depends(require_admin),
):
    return get_scenario_router_ops_incidents_latest(
        target=(target or "win"),
        limit=max(1, min(int(limit), 100)),
    )


@router.get("/research/scenario-router/ops/timeline")
async def get_scenario_router_ops_timeline_api(
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    return get_scenario_router_ops_timeline(
        target=(target or "win"),
        entity_type=str(entity_type or "all"),
        status=str(status or ""),
        since=str(since or ""),
        until=str(until or ""),
        limit=n,
        offset=off,
        sort=str(sort or "desc"),
    )


@router.get("/research/scenario-router/ops/timeline/export")
async def get_scenario_router_ops_timeline_export_api(
    format: str = "json",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    try:
        data = build_scenario_router_ops_timeline_export(
            format=str(format or "json"),
            target=(target or "win"),
            entity_type=str(entity_type or "all"),
            status=str(status or ""),
            since=str(since or ""),
            until=str(until or ""),
            limit=n,
            offset=off,
            sort=str(sort or "desc"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if isinstance(data, dict):
        return data
    text, media_type, filename = data
    return Response(
        content=text,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/research/scenario-router/ops/timeline/{alert_id}/export")
async def get_scenario_router_ops_timeline_by_alert_export_api(
    alert_id: str,
    format: str = "json",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    try:
        data = build_scenario_router_incident_timeline_export(
            alert_id=str(alert_id),
            format=str(format or "json"),
            target=(target or "win"),
            entity_type=str(entity_type or "all"),
            status=str(status or ""),
            since=str(since or ""),
            until=str(until or ""),
            limit=n,
            offset=off,
            sort=str(sort or "desc"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if isinstance(data, dict):
        return data
    text, media_type, filename = data
    return Response(
        content=text,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/research/scenario-router/ops/timeline/report")
async def get_scenario_router_ops_timeline_report_api(
    format: str = "markdown",
    style: str = "default",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    try:
        text, media_type, filename = build_scenario_router_ops_timeline_report(
            format=str(format or "markdown"),
            style=str(style or "default"),
            target=(target or "win"),
            entity_type=str(entity_type or "all"),
            status=str(status or ""),
            since=str(since or ""),
            until=str(until or ""),
            limit=n,
            offset=off,
            sort=str(sort or "desc"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=text,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/research/scenario-router/ops/timeline/{alert_id}/report")
async def get_scenario_router_ops_timeline_by_alert_report_api(
    alert_id: str,
    format: str = "markdown",
    style: str = "default",
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    try:
        text, media_type, filename = build_scenario_router_incident_timeline_report(
            alert_id=str(alert_id),
            format=str(format or "markdown"),
            style=str(style or "default"),
            target=(target or "win"),
            entity_type=str(entity_type or "all"),
            status=str(status or ""),
            since=str(since or ""),
            until=str(until or ""),
            limit=n,
            offset=off,
            sort=str(sort or "desc"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=text,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/research/scenario-router/ops/timeline/{alert_id}.html", response_class=HTMLResponse)
async def get_scenario_router_ops_timeline_by_alert_html_api(
    alert_id: str,
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    html, status = render_scenario_router_ops_timeline_html(
        alert_id=str(alert_id),
        target=(target or "win"),
        entity_type=str(entity_type or "all"),
        status=str(status or ""),
        since=str(since or ""),
        until=str(until or ""),
        limit=n,
        offset=off,
        sort=str(sort or "desc"),
    )
    return HTMLResponse(content=html, status_code=status)


@router.get("/research/scenario-router/ops/timeline/{alert_id}")
async def get_scenario_router_ops_timeline_by_alert_api(
    alert_id: str,
    target: str = "win",
    entity_type: str = "all",
    status: str = "",
    since: str = "",
    until: str = "",
    limit: str = "50",
    offset: str = "0",
    sort: str = "desc",
    _: dict = Depends(require_admin),
):
    n = _q_int(limit, default=50, min_v=1, max_v=200)
    off = _q_int(offset, default=0, min_v=0, max_v=1_000_000)
    return get_scenario_router_incident_timeline(
        alert_id=str(alert_id),
        target=(target or "win"),
        entity_type=str(entity_type or "all"),
        status=str(status or ""),
        since=str(since or ""),
        until=str(until or ""),
        limit=n,
        offset=off,
        sort=str(sort or "desc"),
    )


@router.post("/research/ops/specs/submit")
async def submit_experiment_spec(
    yaml_text: str,
    name: str = "",
    priority: int = 100,
    _: dict = Depends(require_admin),
):
    ops = ExperimentOpsStore()
    data = submit_experiment_yaml(
        yaml_text=yaml_text,
        name=name,
        priority=int(priority),
        metadata={"submitted_via": "api", "source": "mlops_router"},
        store=ops,
    )
    return {
        "success": True,
        **data,
    }


@router.get("/research/ops/queue")
async def list_experiment_queue(
    status: str = "queued",
    limit: int = 50,
    _: dict = Depends(require_admin),
):
    ops = ExperimentOpsStore()
    return {
        "items": ops.list_queue(status=status, limit=limit),
        "status": status,
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/ops/queue/run-next")
async def run_next_experiment(
    worker: str = "api-worker",
    _: dict = Depends(require_admin),
):
    ops = ExperimentOpsStore()
    store = MLOpsStore()
    data = run_next_experiment_job(
        store=ops,
        mlops_db_path=str(store.db_path),
        race_db_path=str(ULTIMATE_DB),
        ultimate_db_path=str(ULTIMATE_DB),
        worker=worker,
    )
    return data


@router.get("/research/ops/runs")
async def list_experiment_runs(
    limit: int = 50,
    _: dict = Depends(require_admin),
):
    ops = ExperimentOpsStore()
    return {
        "items": ops.list_runs(limit=limit),
        "limit": max(1, min(int(limit), 500)),
    }


@router.get("/research/ops/jobs/{job_id}")
async def get_experiment_job(job_id: int, _: dict = Depends(require_admin)):
    ops = ExperimentOpsStore()
    data = ops.get_job(job_id=job_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"job_id not found: {job_id}")
    return data


@router.post("/research/intelligence/plan")
async def plan_research_goal(
    goal_text: str,
    target_roi: float = 0.0,
    scope: str = "all",
    _: dict = Depends(require_admin),
):
    data = plan_experiments_from_goal(
        goal_text=goal_text,
        target_roi=(target_roi if target_roi > 0 else None),
        scope=scope,
    )
    return data


@router.post("/research/intelligence/generate-specs")
async def generate_research_specs(
    goal_text: str,
    baseline_model_id: str,
    challenger_model_ids: str,
    max_specs: int = 20,
    auto_enqueue: bool = False,
    priority_base: int = 100,
    _: dict = Depends(require_admin),
):
    challengers = [s.strip() for s in challenger_model_ids.split(",") if s.strip()]
    plan = plan_experiments_from_goal(goal_text=goal_text)
    specs = generate_experiment_specs(
        plan=plan,
        baseline_model_id=baseline_model_id,
        challenger_model_ids=challengers,
        max_specs=max_specs,
    )

    enqueued: list[dict] = []
    if auto_enqueue:
        ops = ExperimentOpsStore()
        for i, spec in enumerate(specs):
            yaml_text_lines = [
                "experiment:",
                f"  name: {((spec.get('experiment') or {}).get('name') or f'auto_spec_{i+1}')}",
                f"models:",
                f"  baseline_model_id: {((spec.get('models') or {}).get('baseline_model_id') or '')}",
                "  challenger_model_ids:",
            ]
            for m in ((spec.get("models") or {}).get("challenger_model_ids") or []):
                yaml_text_lines.append(f"    - {m}")
            yaml_text_lines.extend(
                [
                    "backtest:",
                    f"  enabled: {str((spec.get('backtest') or {}).get('enabled', True)).lower()}",
                    f"  stake_per_race: {int((spec.get('backtest') or {}).get('stake_per_race', 100))}",
                    "feature_impact:",
                    f"  enabled: {str((spec.get('feature_impact') or {}).get('enabled', True)).lower()}",
                    "  feature_columns:",
                ]
            )
            for c in ((spec.get("feature_impact") or {}).get("feature_columns") or []):
                yaml_text_lines.append(f"    - {c}")
            yaml_text = "\n".join(yaml_text_lines) + "\n"
            data = submit_experiment_yaml(
                yaml_text=yaml_text,
                name=str((spec.get("experiment") or {}).get("name") or f"auto_spec_{i+1}"),
                priority=int(priority_base) - i,
                metadata={"auto_generated": True, "goal_text": goal_text},
                store=ops,
            )
            enqueued.append(data)

    return {
        "plan": plan,
        "spec_count": int(len(specs)),
        "specs": specs,
        "auto_enqueue": bool(auto_enqueue),
        "enqueued": enqueued,
    }


@router.post("/research/intelligence/analyze-job")
async def analyze_research_job(
    job_id: int,
    store_to_kb: bool = True,
    _: dict = Depends(require_admin),
):
    ops = ExperimentOpsStore()
    kb = ResearchKnowledgeBase()
    if store_to_kb:
        return analyze_and_store_job(job_id=job_id, ops_store=ops, kb=kb)
    job = ops.get_job(job_id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"job_id not found: {job_id}")
    from research.experiment_analyzer import analyze_job_result  # type: ignore

    return {
        "success": True,
        **analyze_job_result(job),
    }


@router.get("/research/intelligence/knowledge/signals")
async def list_research_signals(
    metric: str = "",
    limit: int = 100,
    _: dict = Depends(require_admin),
):
    kb = ResearchKnowledgeBase()
    return {
        "items": kb.list_signals(metric=metric, limit=limit),
        "metric": metric,
        "limit": max(1, min(int(limit), 1000)),
    }


@router.get("/research/intelligence/knowledge/recommendations")
async def list_research_recommendations(
    limit: int = 50,
    _: dict = Depends(require_admin),
):
    kb = ResearchKnowledgeBase()
    return {
        "items": kb.list_recommendations(limit=limit),
        "limit": max(1, min(int(limit), 500)),
    }


@router.post("/research/intelligence/recommend")
async def recommend_research_experiments(
    goal_text: str,
    baseline_model_id: str,
    challenger_model_ids: str,
    limit: int = 10,
    _: dict = Depends(require_admin),
):
    challengers = [s.strip() for s in challenger_model_ids.split(",") if s.strip()]
    kb = ResearchKnowledgeBase()
    data = recommend_next_experiments(
        goal_text=goal_text,
        baseline_model_id=baseline_model_id,
        challenger_model_ids=challengers,
        limit=limit,
        kb=kb,
    )
    return data


@router.post("/research/knowledge/pace/rebuild")
async def rebuild_pace_knowledge(
    lookback_per_horse: int = 8,
    _: dict = Depends(require_admin),
):
    data = rebuild_pace_profiles(
        race_db_path=str(ULTIMATE_DB),
        lookback_per_horse=int(max(3, min(int(lookback_per_horse), 20))),
    )
    return {
        "success": True,
        **data,
    }


@router.get("/research/knowledge/pace/races/{race_id}")
async def get_race_pace_intelligence(
    race_id: str,
    auto_rebuild_if_empty: bool = True,
    _: dict = Depends(require_admin),
):
    data = analyze_race_pace(
        race_db_path=str(ULTIMATE_DB),
        race_id=race_id,
        auto_rebuild_if_empty=bool(auto_rebuild_if_empty),
    )
    return data


@router.post("/research/knowledge/bias/rebuild")
async def rebuild_track_bias_knowledge(
    min_races_per_profile: int = 12,
    max_races: int = 0,
    _: dict = Depends(require_admin),
):
    data = rebuild_track_bias_profiles(
        race_db_path=str(ULTIMATE_DB),
        min_races_per_profile=int(max(3, min(int(min_races_per_profile), 200))),
        max_races=int(max(0, min(int(max_races), 200000))),
    )
    return {
        "success": True,
        **data,
    }


@router.get("/research/knowledge/bias/{race_id}")
async def get_track_bias_intelligence(
    race_id: str,
    auto_rebuild_if_empty: bool = True,
    _: dict = Depends(require_admin),
):
    data = analyze_track_bias(
        race_db_path=str(ULTIMATE_DB),
        race_id=race_id,
        auto_rebuild_if_empty=bool(auto_rebuild_if_empty),
    )
    return data


@router.post("/research/knowledge/scenario/rebuild")
async def rebuild_race_scenario_knowledge(
    max_races: int = 0,
    rebuild_dependencies: bool = True,
    _: dict = Depends(require_admin),
):
    data = rebuild_race_scenarios(
        race_db_path=str(ULTIMATE_DB),
        max_races=int(max(0, min(int(max_races), 200000))),
        rebuild_dependencies=bool(rebuild_dependencies),
    )
    return {
        "success": True,
        **data,
    }


@router.get("/research/knowledge/scenario/{race_id}")
async def get_race_scenario_intelligence(
    race_id: str,
    auto_rebuild_if_missing: bool = True,
    _: dict = Depends(require_admin),
):
    data = get_race_scenario(
        race_db_path=str(ULTIMATE_DB),
        race_id=race_id,
        auto_rebuild_if_missing=bool(auto_rebuild_if_missing),
    )
    return data
