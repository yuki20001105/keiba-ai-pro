"""
モデル管理エンドポイント
GET    /api/models
DELETE /api/models/{model_id}
GET    /api/models/{model_id}
PUT    /api/models/{model_id}/activate
GET    /api/models/active
"""
from __future__ import annotations

from datetime import datetime
import joblib
import json
import os
from pathlib import Path
import re
from typing import Mapping

from fastapi import APIRouter, Depends, HTTPException

from deps.auth import require_admin  # type: ignore

from app_config import (  # type: ignore
    SUPABASE_DATA_ENABLED,
    MODELS_DIR,
    get_supabase_client,
    load_model_bundle,
    _ensure_model_local,
    get_active_model_id,
    set_active_model_id,
    get_latest_model,
)

router = APIRouter()
_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test", "ci"})
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_CANDIDATE_EVALUATION_PINS = {
    "local_safe_m01_2026": "70e6f35c3c842d2fee7b7abf42c5d9308bc2db29568f50e3f08d8d61576c2670",
    "local_safe_m02_2025": "ee5f99748e3de5c2db15a9e4ac8db3ba294bd55b9d9f7d16f087bdcf0b148241",
}
_CANDIDATE_EVALUATION_PERIODS = {
    # Actual fixed-fold prediction dates, not the full evaluation-year label.
    "local_safe_m01_2026": ("20260104", "20260913"),
    "local_safe_m02_2025": ("20250105", "20251228"),
}


def _load_managed_bundle(path: Path) -> dict:
    if path.name.startswith("model_win_reference_"):
        from services.win_nll_reference_input import load_published_bundle
        return load_published_bundle(path)
    return joblib.load(path)


def _local_candidate_models() -> list[dict]:
    """Pinned local research selections, outside the legacy model directory."""
    from app_config import APP_ENV
    if APP_ENV not in {"development", "local", "test"}:
        return []
    import hashlib
    from services.local_candidate_input import candidate_specs, load_candidate_bundle
    models = []
    for spec in candidate_specs():
        try:
            path = Path(spec["source_path"])
            bundle = load_candidate_bundle(path, model_id=spec["model_id"])
            evidence = path.parent / "result.json"
            body = evidence.read_bytes()
            if hashlib.sha256(body).hexdigest() != _CANDIDATE_EVALUATION_PINS[spec["model_id"]]:
                continue
            fold = next(row for row in json.loads(body)["folds"]
                        if row["year"] == spec["evaluation_year"] and row["sha256"] == spec["sha256"])
            scores = fold["evaluation"]["overall"]
            year = str(spec["evaluation_year"])
            date_from, date_to = _CANDIDATE_EVALUATION_PERIODS[spec["model_id"]]
            evaluation = {
                "schema": "model-evaluation-v1", "target": "speed_deviation", "rmse_unit": "m/s",
                "primary": {"rank_correlation": scores["rank_correlation"], "rmse": scores["rmse_speed_mps"],
                            "top_pick_win_rate": scores["top1_win_rate"], "win_roi": None},
                "details": {"mae": scores["mae_speed_mps"], "r2": None,
                            "evaluation_date_from": date_from, "evaluation_date_to": date_to,
                            "evaluation_race_count": scores["race_count"]},
                "evaluation_label": "既知期間の事後検証 · 回収率は別評価",
                "time_slices": [{"period": year, "race_count": scores["race_count"],
                                 "rank_correlation": scores["rank_correlation"], "rmse": scores["rmse_speed_mps"]}],
            }
            models.append({"model_id": spec["model_id"], "model_path": str(path), "display_name": spec["label"],
                           "target": bundle["target"], "model_type": "lightgbm", "created_at": "unknown",
                           "ultimate_mode": True, "feature_count": spec["feature_count"], "is_active": False,
                           "is_local_candidate": True, "can_activate": False, "can_delete": False,
                           "training_date_from": spec["train_period"][0], "training_date_to": spec["train_period"][1],
                           "n_rows": fold["train_rows"], "race_count": fold["train_races"], "evaluation": evaluation,
                           "artifact_sha256": spec["sha256"]})
        except (OSError, ValueError, KeyError, StopIteration, TypeError):
            # No stale metadata is a substitute for a verified artifact.
            continue
    return models


def _model_evaluation(model_path: Path, bundle: Mapping[str, object]) -> dict[str, object]:
    """Return persisted evaluation data without recalculating a model on page load."""

    embedded = bundle.get("evaluation")
    if isinstance(embedded, Mapping):
        return dict(embedded)

    sidecar_path = model_path.with_suffix(".evaluation.json")
    try:
        if sidecar_path.is_file() and 0 < sidecar_path.stat().st_size <= 256 * 1024:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if (
                isinstance(sidecar, dict)
                and sidecar.get("model_id") == model_path.stem
                and isinstance(sidecar.get("evaluation"), dict)
            ):
                return sidecar["evaluation"]
    except (OSError, ValueError, TypeError):
        pass

    metrics = bundle.get("metrics")
    if bundle.get("target") == "speed_deviation" and isinstance(metrics, Mapping):
        # Older regression bundles used classification-shaped field names.
        # Keep them readable while clearly leaving unmeasured fields empty.
        return {
            "schema": "model-evaluation-v1",
            "target": "speed_deviation",
            "primary": {
                "rank_correlation": metrics.get("auc"),
                "rmse": metrics.get("logloss"),
                "top_pick_win_rate": None,
                "favorite_win_rate": None,
                "top_pick_win_rate_delta": None,
                "win_roi": None,
            },
            "details": {},
            "time_slices": [],
        }
    return {}


def _model_created_sort_key(model_path: Path, bundle: Mapping[str, object]) -> float:
    """Return the actual model creation time, falling back to file mtime."""

    raw_created_at = bundle.get("created_at")
    if isinstance(raw_created_at, str):
        value = raw_created_at.strip()
        for date_format in ("%Y%m%d_%H%M%S", "%Y%m%d_%H%M"):
            try:
                return datetime.strptime(value, date_format).timestamp()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return model_path.stat().st_mtime


def _require_legacy_model_deletion_allowed() -> None:
    """Keep direct artifact deletion behind explicit local/test compatibility."""

    environment = (os.environ.get("APP_ENV") or "").strip().lower()
    enabled = (os.environ.get("MODEL_DELETION_LOCAL_ENABLED") or "").strip().lower() == "true"
    if environment not in _LOCAL_ENVIRONMENTS or not enabled:
        raise HTTPException(
            status_code=409,
            detail=(
                "model deletion requires a separate durable retirement approval; "
                "legacy deletion is available only by explicit local/test opt-in"
            ),
        )


@router.get("/api/models")
async def list_models(ultimate: bool | None = None):
    """保存済みモデルの一覧を取得
    
    - ultimate=true  : ultimate_mode=True のモデルのみ
    - ultimate=false : ultimate_mode=False のモデルのみ
    - 未指定          : 全モデルを返す
    """
    try:
        # ローカルモデルを優先スキャン
        local_files = list(MODELS_DIR.glob("model_*.joblib"))

        if not local_files and SUPABASE_DATA_ENABLED and get_supabase_client():
            # ローカルに何もない場合のみ Supabase にフォールバック
            from app_config import list_models_from_supabase  # type: ignore
            sb_models = list_models_from_supabase()
            if ultimate is not None:
                sb_models = [m for m in sb_models if m.get("ultimate_mode", False) == ultimate]
            if ultimate is not False:
                sb_models += _local_candidate_models()
            return {"models": sb_models, "count": len(sb_models)}

        # アクティブモデル ID を取得（未設定なら latest を使う）
        active_id = get_active_model_id()
        if active_id is None:
            latest = get_latest_model()
            active_id = latest.stem if latest else None

        models_with_sort_keys: list[tuple[float, dict[str, object]]] = []
        for model_path in local_files:
            try:
                bundle = _load_managed_bundle(model_path)
                is_ultimate = bundle.get("ultimate_mode", False)
                # ultimate フィルタ（None = 全件返す）
                if ultimate is not None and is_ultimate != ultimate:
                    continue
                feat_count = (
                    len(bundle.get("feature_columns") or [])
                    or len(bundle.get("feature_cols_num") or []) + len(bundle.get("feature_cols_cat") or [])
                )
                model_id = model_path.stem
                evaluation = _model_evaluation(model_path, bundle)
                model = {
                    "model_id": model_id,
                    "model_path": str(model_path),
                    "created_at": bundle.get("created_at", "unknown"),
                    "target": bundle.get("target", "unknown"),
                    "model_type": bundle.get("model_type", "unknown"),
                    "ultimate_mode": is_ultimate,
                    "use_optimizer": bundle.get("use_optimizer", False),
                    "auc": bundle.get("metrics", {}).get("auc", 0.0),
                    "cv_auc_mean": bundle.get("metrics", {}).get("cv_auc_mean", 0.0),
                    "evaluation": evaluation,
                    "training_date_from": bundle.get("training_date_from"),
                    "training_date_to": bundle.get("training_date_to"),
                    "n_rows": bundle.get("data_count", 0),
                    "race_count": bundle.get("race_count", 0),
                    "feature_count": feat_count,
                    "feature_pipeline": bundle.get("feature_pipeline"),
                    "probability_validation": bundle.get("probability_validation", {}),
                    "training_time_seconds": bundle.get("training_time_seconds"),
                    "training_parallel_workers": bundle.get("training_parallel_workers"),
                    "native_threads": bundle.get("native_threads"),
                    "acceleration": {key: bundle.get("acceleration", {}).get(key)
                        for key in ("device", "requested_device", "cache_hit", "cache_status",
                                    "preparation_seconds", "fit_seconds", "total_seconds", "fallback_reason")}
                        if isinstance(bundle.get("acceleration"), dict) else {},
                    "is_active": model_id == active_id,
                }
                if bundle.get("_win_nll_reference") is True:
                    model.update(display_name=bundle.get("display_name", "全履歴・勝率NLL（参考運用）"),
                                 reference_only=True, can_delete=False)
                elif bundle.get("feature_pipeline") == "local-db-context-v1":
                    model.update(display_name="条件・展開80（比較候補）", reference_only=True)
                models_with_sort_keys.append(
                    (_model_created_sort_key(model_path, bundle), model)
                )
            except Exception as e:
                print(f"モデル読み込みエラー {model_path}: {e}")
                continue

        # 学習期間が先頭に入る model_id 順では、新しく作成した長期間モデルが
        # 古く見えるため、bundle の作成日時を正本として最新順に並べる。
        models_with_sort_keys.sort(
            key=lambda item: (item[0], str(item[1].get("model_id", ""))),
            reverse=True,
        )
        models = [model for _sort_key, model in models_with_sort_keys]
        if ultimate is not False:
            models += _local_candidate_models()
        return {"models": models, "count": len(models)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル一覧取得エラー: {str(e)}")


@router.delete("/api/models/{model_id}")
async def delete_model(model_id: str, _: dict = Depends(require_admin)):
    """保存済みモデルを削除"""
    if model_id.startswith("local_safe_"):
        raise HTTPException(status_code=409, detail="研究候補の原本は削除できません")
    if model_id.startswith("model_win_reference_"):
        raise HTTPException(status_code=409, detail="封印済みの参考運用モデルは削除できません")
    _require_legacy_model_deletion_allowed()
    if not _MODEL_ID_PATTERN.fullmatch(model_id):
        raise HTTPException(status_code=400, detail="モデルIDが不正です")

    active_id = get_active_model_id()
    if active_id is None:
        latest = get_latest_model()
        active_id = latest.stem if latest else None
    if model_id == active_id:
        raise HTTPException(
            status_code=409,
            detail="使用中モデルは削除できません。先に別のモデルを既定にしてください。",
        )

    try:
        deleted = []
        if SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import delete_model_from_supabase  # type: ignore
            # 戻り値を確認: False = モデルが Supabase に存在しない
            if delete_model_from_supabase(model_id):
                deleted.append(f"supabase:{model_id}")
        model_path = MODELS_DIR / f"{model_id}.joblib"
        if model_path.is_file():
            model_path.unlink()
            deleted.append(model_path.name)
            evaluation_path = model_path.with_suffix(".evaluation.json")
            if evaluation_path.is_file():
                evaluation_path.unlink()
                deleted.append(evaluation_path.name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        return {"success": True, "deleted": deleted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"削除エラー: {str(e)}")


@router.get("/api/models/{model_id}")
async def get_model_info(model_id: str):
    """特定のモデル情報を取得"""
    try:
        if model_id.startswith("local_safe_"):
            for model in _local_candidate_models():
                if model["model_id"] == model_id:
                    return {"success": True, **model}
            raise HTTPException(status_code=404, detail="検証済みのローカル候補が見つかりません")
        model_files = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
        if not model_files:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        bundle = (_load_managed_bundle(model_files[0]) if model_files[0].name.startswith("model_win_reference_")
                  else load_model_bundle(model_files[0]))
        return {
            "success": True,
            "model_id": model_id,
            "model_path": str(model_files[0]),
            "created_at": bundle.get("created_at", "unknown"),
            "target": bundle.get("target", "unknown"),
            "model_type": bundle.get("model_type", "unknown"),
            "metrics": bundle.get("metrics", {}),
            "evaluation": _model_evaluation(model_files[0], bundle),
            "feature_pipeline": bundle.get("feature_pipeline"),
            "probability_validation": bundle.get("probability_validation", {}),
            "display_name": bundle.get("display_name"),
            "reference_only": bundle.get("reference_only", False),
            "data_count": bundle.get("data_count", 0),
            "race_count": bundle.get("race_count", 0),
                "feature_count": (
                    len(bundle.get("feature_columns") or [])
                    or len(bundle.get("feature_cols_num") or []) + len(bundle.get("feature_cols_cat") or [])
                ),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル情報の取得に失敗: {str(e)}")


@router.get("/api/models/active/info")
async def get_active_model():
    """現在アクティブなモデルの情報を返す"""
    active_id = get_active_model_id()
    if active_id is None:
        latest = get_latest_model()
        if latest is None:
            raise HTTPException(status_code=404, detail="モデルが見つかりません")
        active_id = latest.stem

    model_path = MODELS_DIR / f"{active_id}.joblib"
    if not model_path.exists():
        raise HTTPException(status_code=404, detail=f"アクティブモデル {active_id} が見つかりません")

    try:
        bundle = _load_managed_bundle(model_path)
        feat_count = (
            len(bundle.get("feature_columns") or [])
            or len(bundle.get("feature_cols_num") or []) + len(bundle.get("feature_cols_cat") or [])
        )
        return {
            "model_id": active_id,
            "target": bundle.get("target", "unknown"),
            "model_type": bundle.get("model_type", "unknown"),
            "created_at": bundle.get("created_at", "unknown"),
            "training_date_from": bundle.get("training_date_from"),
            "training_date_to": bundle.get("training_date_to"),
            "auc": bundle.get("metrics", {}).get("auc", 0.0),
            "cv_auc_mean": bundle.get("metrics", {}).get("cv_auc_mean", 0.0),
            "evaluation": _model_evaluation(model_path, bundle),
            "feature_pipeline": bundle.get("feature_pipeline"),
            "probability_validation": bundle.get("probability_validation", {}),
            "display_name": bundle.get("display_name"),
            "reference_only": bundle.get("reference_only", False),
            "feature_count": feat_count,
            "n_rows": bundle.get("data_count", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"アクティブモデル情報の取得に失敗: {str(e)}")


@router.put("/api/models/{model_id}/activate")
async def activate_model(model_id: str, _: dict = Depends(require_admin)):
    """指定したモデルをアクティブにする（予測に使用するモデルを切り替える）"""
    if model_id.startswith("local_safe_"):
        raise HTTPException(status_code=409, detail="研究候補は明示選択専用です。既定モデルは維持します")
    environment = (os.environ.get("APP_ENV") or "").strip().lower()
    local_environments = {"local", "development", "dev", "test", "ci"}
    legacy_opt_in = (
        os.environ.get("MODEL_ACTIVATION_LOCAL_ENABLED") or ""
    ).strip().lower() == "true"
    if environment not in local_environments or not legacy_opt_in:
        raise HTTPException(
            status_code=409,
            detail=(
                "active model switch requires a separate durable approval; "
                "legacy activation is available only by explicit local/test opt-in"
            ),
        )
    model_path = MODELS_DIR / f"{model_id}.joblib"
    if not model_path.exists():
        # 部分一致でも探す
        candidates = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
        if not candidates:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        model_path = candidates[0]
        model_id = model_path.stem

    try:
        if model_path.name.startswith("model_win_reference_"):
            _load_managed_bundle(model_path)
        set_active_model_id(model_id)
        return {"success": True, "active_model_id": model_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"アクティブモデルの切り替えに失敗: {str(e)}")
