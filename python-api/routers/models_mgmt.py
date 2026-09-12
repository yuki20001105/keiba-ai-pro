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
            return {"models": sb_models, "count": len(sb_models)}

        # アクティブモデル ID を取得（未設定なら latest を使う）
        active_id = get_active_model_id()
        if active_id is None:
            latest = get_latest_model()
            active_id = latest.stem if latest else None

        models_with_sort_keys: list[tuple[float, dict[str, object]]] = []
        for model_path in local_files:
            try:
                bundle = joblib.load(model_path)
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
                    "is_active": model_id == active_id,
                }
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
        return {"models": models, "count": len(models)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル一覧取得エラー: {str(e)}")


@router.delete("/api/models/{model_id}")
async def delete_model(model_id: str, _: dict = Depends(require_admin)):
    """保存済みモデルを削除"""
    _require_legacy_model_deletion_allowed()
    try:
        deleted = []
        if SUPABASE_DATA_ENABLED and get_supabase_client():
            from app_config import delete_model_from_supabase  # type: ignore
            # 戻り値を確認: False = モデルが Supabase に存在しない
            if delete_model_from_supabase(model_id):
                deleted.append(f"supabase:{model_id}")
        for f in MODELS_DIR.glob(f"*{model_id}*.joblib"):
            f.unlink()
            deleted.append(f.name)
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
        model_files = list(MODELS_DIR.glob(f"*{model_id}*.joblib"))
        if not model_files:
            raise HTTPException(status_code=404, detail=f"モデル {model_id} が見つかりません")
        bundle = load_model_bundle(model_files[0])
        return {
            "success": True,
            "model_id": model_id,
            "model_path": str(model_files[0]),
            "created_at": bundle.get("created_at", "unknown"),
            "target": bundle.get("target", "unknown"),
            "model_type": bundle.get("model_type", "unknown"),
            "metrics": bundle.get("metrics", {}),
            "evaluation": _model_evaluation(model_files[0], bundle),
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
        bundle = joblib.load(model_path)
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
            "feature_count": feat_count,
            "n_rows": bundle.get("data_count", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"アクティブモデル情報の取得に失敗: {str(e)}")


@router.put("/api/models/{model_id}/activate")
async def activate_model(model_id: str, _: dict = Depends(require_admin)):
    """指定したモデルをアクティブにする（予測に使用するモデルを切り替える）"""
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
        set_active_model_id(model_id)
        return {"success": True, "active_model_id": model_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"アクティブモデルの切り替えに失敗: {str(e)}")
