from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd


def build_feature_importance_df(model) -> pd.DataFrame:
    if hasattr(model, "feature_importance") and hasattr(model, "feature_name"):
        gain = model.feature_importance(importance_type="gain")
        split = model.feature_importance(importance_type="split")
        names = model.feature_name()
        return pd.DataFrame({"feature": names, "gain": gain, "split": split}).sort_values("gain", ascending=False).reset_index(drop=True)

    if hasattr(model, "booster_"):
        booster = model.booster_
        gain = booster.feature_importance(importance_type="gain")
        split = booster.feature_importance(importance_type="split")
        names = booster.feature_name()
        return pd.DataFrame({"feature": names, "gain": gain, "split": split}).sort_values("gain", ascending=False).reset_index(drop=True)

    return pd.DataFrame(columns=["feature", "gain", "split"])


def build_model_bundle(**kwargs) -> dict:
    bundle = dict(kwargs)
    bundle["saved_at"] = datetime.utcnow().isoformat()
    return bundle


def save_model_bundle(*, bundle: dict, target: str, models_dir: Path, feature_importance: pd.DataFrame, save_store_fn, model_store: Path, feature_store: Path) -> dict:
    models_dir = Path(models_dir)
    model_store = Path(model_store)
    feature_store = Path(feature_store)
    models_dir.mkdir(parents=True, exist_ok=True)
    model_store.mkdir(parents=True, exist_ok=True)
    feature_store.mkdir(parents=True, exist_ok=True)

    model_key = f"lgb_model_{target}"
    model_path = models_dir / f"{model_key}.pkl"
    meta_path = models_dir / f"{model_key}.metadata.json"

    joblib.dump(bundle, model_path)
    meta_payload = {
        "target": target,
        "task": bundle.get("task"),
        "best_iteration": bundle.get("best_iteration"),
        "metrics": bundle.get("metrics", {}),
        "saved_at": bundle.get("saved_at"),
    }
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    save_store_fn(bundle, model_store, model_key)
    if isinstance(feature_importance, pd.DataFrame):
        save_store_fn(feature_importance, feature_store, "feature_importance")
    return {"model_path": model_path, "meta_path": meta_path}
