from __future__ import annotations

from pathlib import Path
from typing import Any

from feature_platform.discovery import run_feature_discovery  # type: ignore
from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
from knowledge.scenario_engine import (  # type: ignore
    attach_scenario_features_to_frame,
    get_race_scenario,
    rebuild_race_scenarios,
    suggest_scenario_interaction_nodes,
)
from mlops.backtest import run_backtest  # type: ignore

from .experiment_lab import run_experiment_lab
from .feature_impact import run_feature_impact_analysis


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return []


def _as_segment_list(v: Any) -> list[str]:
    allowed = {"expected_pace", "expected_bias", "winning_pattern"}
    return [x for x in _as_list(v) if x in allowed]


def normalize_experiment_spec(spec: dict[str, Any]) -> dict[str, Any]:
    exp = spec.get("experiment") if isinstance(spec.get("experiment"), dict) else {}
    models = spec.get("models") if isinstance(spec.get("models"), dict) else {}
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    discovery = spec.get("feature_discovery") if isinstance(spec.get("feature_discovery"), dict) else {}
    backtest = spec.get("backtest") if isinstance(spec.get("backtest"), dict) else {}
    impact = spec.get("feature_impact") if isinstance(spec.get("feature_impact"), dict) else {}
    lab = spec.get("experiment_lab") if isinstance(spec.get("experiment_lab"), dict) else {}
    scenario = spec.get("scenario") if isinstance(spec.get("scenario"), dict) else {}

    out = {
        "experiment": {
            "name": str(exp.get("name") or "unnamed_experiment"),
            "tags": _as_list(exp.get("tags") or []),
        },
        "data": {
            "date_from": (str(data.get("date_from")) if data.get("date_from") else None),
            "date_to": (str(data.get("date_to")) if data.get("date_to") else None),
            "limit_rows": int(data.get("limit_rows", 12000)),
            "reference_race_ids": _as_list(data.get("reference_race_ids") or []),
        },
        "models": {
            "baseline_model_id": str(models.get("baseline_model_id") or ""),
            "challenger_model_ids": _as_list(models.get("challenger_model_ids") or []),
        },
        "feature_discovery": {
            "enabled": bool(discovery.get("enabled", False)),
            "target_col": str(discovery.get("target_col") or "win"),
            "max_candidates": int(discovery.get("max_candidates", 120)),
            "top_k": int(discovery.get("top_k", 20)),
            "min_total_score": float(discovery.get("min_total_score", 20.0)),
            "promote": bool(discovery.get("promote", False)),
        },
        "backtest": {
            "enabled": bool(backtest.get("enabled", True)),
            "model_ids": _as_list(backtest.get("model_ids") or []),
            "stake_per_race": int(backtest.get("stake_per_race", 100)),
        },
        "feature_impact": {
            "enabled": bool(impact.get("enabled", True)),
            "feature_columns": _as_list(impact.get("feature_columns") or []),
            "max_predictions": int(impact.get("max_predictions", 5000)),
            "min_group_size": int(impact.get("min_group_size", 20)),
        },
        "experiment_lab": {
            "enabled": bool(lab.get("enabled", True)),
            "max_predictions": int(lab.get("max_predictions", 5000)),
            "bootstrap_iters": int(lab.get("bootstrap_iters", 3000)),
            "permutation_iters": int(lab.get("permutation_iters", 5000)),
            "min_segment_overlap": int(lab.get("min_segment_overlap", 20)),
            "scenario_segment_by": _as_segment_list(
                lab.get("scenario_segment_by")
                or ["expected_pace", "expected_bias", "winning_pattern"]
            ),
        },
        "scenario": {
            "enabled": bool(scenario.get("enabled", True)),
            "rebuild_before_run": bool(scenario.get("rebuild_before_run", False)),
            "use_for_discovery": bool(scenario.get("use_for_discovery", True)),
            "include_interactions": bool(scenario.get("include_interactions", True)),
            "max_interaction_nodes": int(scenario.get("max_interaction_nodes", 24)),
        },
    }
    return out


def run_experiment_spec(
    spec: dict[str, Any],
    *,
    mlops_db_path: str,
    race_db_path: str,
    ultimate_db_path: str,
) -> dict[str, Any]:
    cfg = normalize_experiment_spec(spec)
    out: dict[str, Any] = {
        "experiment": cfg.get("experiment") or {},
        "spec": cfg,
        "stages": {},
    }

    date_from = (cfg.get("data") or {}).get("date_from")
    date_to = (cfg.get("data") or {}).get("date_to")
    stake = int((cfg.get("backtest") or {}).get("stake_per_race", 100))
    scenario_cfg = cfg.get("scenario") if isinstance(cfg.get("scenario"), dict) else {}

    baseline_model_id = str((cfg.get("models") or {}).get("baseline_model_id") or "")
    challenger_model_ids = _as_list((cfg.get("models") or {}).get("challenger_model_ids") or [])

    if bool(scenario_cfg.get("enabled")) and bool(scenario_cfg.get("rebuild_before_run")):
        scen_rebuild = rebuild_race_scenarios(
            race_db_path=race_db_path,
            knowledge_db_path=None,
            max_races=0,
            rebuild_dependencies=False,
        )
        out["stages"]["scenario_rebuild"] = scen_rebuild

    ref_ids = _as_list((cfg.get("data") or {}).get("reference_race_ids") or [])
    if bool(scenario_cfg.get("enabled")) and ref_ids:
        out["stages"]["scenario_samples"] = {
            "items": [
                get_race_scenario(race_db_path=race_db_path, race_id=rid, auto_rebuild_if_missing=True)
                for rid in ref_ids[:10]
            ]
        }

    if bool((cfg.get("feature_discovery") or {}).get("enabled")):
        n = max(1000, min(int((cfg.get("data") or {}).get("limit_rows", 12000)), 120000))
        df = load_ultimate_training_frame(Path(ultimate_db_path))
        if len(df) > n:
            df = df.head(n)

        scenario_cols: list[str] = []
        extra_nodes: list[dict] = []
        if bool(scenario_cfg.get("enabled")) and bool(scenario_cfg.get("use_for_discovery")):
            df, scenario_cols = attach_scenario_features_to_frame(
                df=df,
                race_db_path=race_db_path,
                race_id_col="race_id",
            )
            if bool(scenario_cfg.get("include_interactions")):
                extra_nodes = suggest_scenario_interaction_nodes(
                    df,
                    max_nodes=int(scenario_cfg.get("max_interaction_nodes", 24)),
                )

        discovery = run_feature_discovery(
            df=df,
            target_col=str((cfg.get("feature_discovery") or {}).get("target_col") or "win"),
            max_candidates=int((cfg.get("feature_discovery") or {}).get("max_candidates", 120)),
            top_k=int((cfg.get("feature_discovery") or {}).get("top_k", 20)),
            min_total_score=float((cfg.get("feature_discovery") or {}).get("min_total_score", 20.0)),
            promote=bool((cfg.get("feature_discovery") or {}).get("promote", False)),
            extra_nodes=extra_nodes,
        )
        discovery["scenario"] = {
            "included": bool(scenario_cols),
            "scenario_feature_columns": scenario_cols,
            "scenario_interaction_candidates": int(len(extra_nodes)),
        }
        out["stages"]["feature_discovery"] = discovery

    bt_model_ids = _as_list((cfg.get("backtest") or {}).get("model_ids") or [])
    if not bt_model_ids:
        bt_model_ids = [m for m in [baseline_model_id, *challenger_model_ids] if m]
    if bool((cfg.get("backtest") or {}).get("enabled")) and bt_model_ids:
        backtest = run_backtest(
            mlops_db_path=mlops_db_path,
            race_db_path=race_db_path,
            model_ids=bt_model_ids,
            date_from=(str(date_from) if date_from else None),
            date_to=(str(date_to) if date_to else None),
            stake_per_race=int(stake),
        )
        out["stages"]["backtest"] = backtest

    if bool((cfg.get("feature_impact") or {}).get("enabled")):
        impact = run_feature_impact_analysis(
            mlops_db_path=mlops_db_path,
            race_db_path=race_db_path,
            model_ids=(bt_model_ids or None),
            date_from=(str(date_from) if date_from else None),
            date_to=(str(date_to) if date_to else None),
            stake_per_race=int(stake),
            feature_columns=_as_list((cfg.get("feature_impact") or {}).get("feature_columns") or []),
            max_predictions=int((cfg.get("feature_impact") or {}).get("max_predictions", 5000)),
            min_group_size=int((cfg.get("feature_impact") or {}).get("min_group_size", 20)),
        )
        out["stages"]["feature_impact"] = impact

    if (
        bool((cfg.get("experiment_lab") or {}).get("enabled"))
        and baseline_model_id
        and challenger_model_ids
    ):
        lab = run_experiment_lab(
            mlops_db_path=mlops_db_path,
            race_db_path=race_db_path,
            baseline_model_id=baseline_model_id,
            challenger_model_ids=challenger_model_ids,
            date_from=(str(date_from) if date_from else None),
            date_to=(str(date_to) if date_to else None),
            stake_per_race=int(stake),
            max_predictions=int((cfg.get("experiment_lab") or {}).get("max_predictions", 5000)),
            bootstrap_iters=int((cfg.get("experiment_lab") or {}).get("bootstrap_iters", 3000)),
            permutation_iters=int((cfg.get("experiment_lab") or {}).get("permutation_iters", 5000)),
            min_segment_overlap=int((cfg.get("experiment_lab") or {}).get("min_segment_overlap", 20)),
            scenario_segment_by=_as_segment_list(
                (cfg.get("experiment_lab") or {}).get("scenario_segment_by")
                or ["expected_pace", "expected_bias", "winning_pattern"]
            ),
        )
        out["stages"]["experiment_lab"] = lab

    out["summary"] = {
        "stage_count": int(len(out.get("stages") or {})),
        "stages": sorted(list((out.get("stages") or {}).keys())),
        "baseline_model_id": baseline_model_id,
        "challenger_count": int(len(challenger_model_ids)),
    }
    return out
