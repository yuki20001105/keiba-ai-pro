"""
モデル管理エンドポイント
GET    /api/models
DELETE /api/models/{model_id}
GET    /api/models/{model_id}
PUT    /api/models/{model_id}/activate
GET    /api/models/active
"""
from __future__ import annotations

import joblib
from fastapi import APIRouter, HTTPException

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

        models = []
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
                models.append({
                    "model_id": model_id,
                    "model_path": str(model_path),
                    "created_at": bundle.get("created_at", "unknown"),
                    "target": bundle.get("target", "unknown"),
                    "model_type": bundle.get("model_type", "unknown"),
                    "ultimate_mode": is_ultimate,
                    "use_optimizer": bundle.get("use_optimizer", False),
                    "auc": bundle.get("metrics", {}).get("auc", 0.0),
                    "cv_auc_mean": bundle.get("metrics", {}).get("cv_auc_mean", 0.0),
                    "training_date_from": bundle.get("training_date_from"),
                    "training_date_to": bundle.get("training_date_to"),
                    "n_rows": bundle.get("data_count", 0),
                    "feature_count": feat_count,
                    "is_active": model_id == active_id,
                })
            except Exception as e:
                print(f"モデル読み込みエラー {model_path}: {e}")
                continue

        # model_id は末尾に YYYYMMDD_HHMMSS を含む形式。
        # 降順ソートで最新モデルが先頭に来る。
        models.sort(key=lambda x: x.get("model_id", ""), reverse=True)
        return {"models": models, "count": len(models)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデル一覧取得エラー: {str(e)}")


@router.delete("/api/models/{model_id}")
async def delete_model(model_id: str):
    """保存済みモデルを削除"""
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
            "feature_count": feat_count,
            "n_rows": bundle.get("data_count", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"アクティブモデル情報の取得に失敗: {str(e)}")


@router.put("/api/models/{model_id}/activate")
async def activate_model(model_id: str):
    """指定したモデルをアクティブにする（予測に使用するモデルを切り替える）"""
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
