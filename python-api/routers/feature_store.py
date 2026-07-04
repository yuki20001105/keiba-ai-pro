from __future__ import annotations

from fastapi import APIRouter, Depends

from app_config import ULTIMATE_DB  # type: ignore
from deps.auth import require_admin  # type: ignore
from feature_platform import FeatureStoreManager  # type: ignore
from feature_platform.discovery import run_feature_discovery  # type: ignore
from feature_platform.generator import apply_feature_generator  # type: ignore
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
from knowledge.scenario_engine import (  # type: ignore
    attach_scenario_features_to_frame,
    suggest_scenario_interaction_nodes,
)
from mlops import MLOpsStore  # type: ignore
from mlops.backtest import run_backtest  # type: ignore

router = APIRouter(prefix="/api/feature-store", tags=["feature-store"])


@router.get("/versions")
async def list_feature_versions(limit: int = 20, _: dict = Depends(require_admin)):
    mgr = FeatureStoreManager()
    return {
        "limit": max(1, min(int(limit), 200)),
        "items": mgr.list_feature_sets(limit=limit),
    }


@router.get("/quality/latest")
async def latest_feature_quality(_: dict = Depends(require_admin)):
    mgr = FeatureStoreManager()
    data = mgr.get_latest_quality()
    return data or {"message": "no_feature_set_available"}


@router.get("/gate")
async def feature_store_gate(
    min_score: float = 95.0,
    max_validation_errors: int = 0,
    _: dict = Depends(require_admin),
):
    mgr = FeatureStoreManager()
    return mgr.evaluate_gate(min_score=min_score, max_validation_errors=max_validation_errors)


@router.post("/generate")
async def generate_features_preview(
    limit_rows: int = 5000,
    _: dict = Depends(require_admin),
):
    """Feature DSL を適用して生成特徴量をプレビューする。"""
    n = max(100, min(int(limit_rows), 50000))
    df = load_ultimate_training_frame(ULTIMATE_DB)
    if len(df) > n:
        df = df.head(n)
    out, created = apply_feature_generator(df)
    sample_cols = [c for c in created[:20] if c in out.columns]
    sample = out[sample_cols].head(5).to_dict(orient="records") if sample_cols else []
    return {
        "input_rows": int(len(df)),
        "generated_features": created,
        "generated_count": int(len(created)),
        "sample": sample,
    }


@router.post("/discovery/run")
async def run_feature_discovery_engine(
    target_col: str = "win",
    limit_rows: int = 12000,
    max_candidates: int = 120,
    top_k: int = 20,
    min_total_score: float = 20.0,
    promote: bool = False,
    include_scenario_features: bool = False,
    include_scenario_interactions: bool = False,
    scenario_max_interactions: int = 24,
    run_backtest_after: bool = False,
    model_ids: str = "",
    date_from: str = "",
    date_to: str = "",
    stake_per_race: int = 100,
    _: dict = Depends(require_admin),
):
    n = max(1000, min(int(limit_rows), 120000))
    df = load_ultimate_training_frame(ULTIMATE_DB)
    if len(df) > n:
        df = df.head(n)

    scenario_cols: list[str] = []
    extra_nodes: list[dict] = []
    if include_scenario_features or include_scenario_interactions:
        df, scenario_cols = attach_scenario_features_to_frame(
            df=df,
            race_db_path=str(ULTIMATE_DB),
            race_id_col="race_id",
        )
    if include_scenario_interactions:
        extra_nodes = suggest_scenario_interaction_nodes(df, max_nodes=int(scenario_max_interactions))

    discovery = run_feature_discovery(
        df=df,
        target_col=target_col,
        max_candidates=max_candidates,
        top_k=top_k,
        min_total_score=min_total_score,
        promote=promote,
        extra_nodes=extra_nodes,
    )

    backtest_result = None
    if run_backtest_after:
        store = MLOpsStore()
        mids = [s.strip() for s in model_ids.split(",") if s.strip()]
        backtest_result = run_backtest(
            mlops_db_path=str(store.db_path),
            race_db_path=str(ULTIMATE_DB),
            model_ids=(mids or None),
            date_from=(date_from or None),
            date_to=(date_to or None),
            stake_per_race=int(stake_per_race),
        )

    return {
        "discovery": discovery,
        "scenario": {
            "included_features": bool(include_scenario_features or include_scenario_interactions),
            "scenario_feature_columns": scenario_cols,
            "scenario_interaction_candidates": int(len(extra_nodes)),
        },
        "backtest": backtest_result,
    }
