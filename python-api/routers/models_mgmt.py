"""
モデル管理エンドポイント
GET    /api/models
DELETE /api/models/{model_id}
GET    /api/models/{model_id}
"""
from __future__ import annotations

import joblib
from fastapi import APIRouter, HTTPException

from app_config import (  # type: ignore
    SUPABASE_ENABLED,
    MODELS_DIR,
    get_supabase_client,
    load_model_bundle,
    _ensure_model_local,
)

router = APIRouter()


@router.get("/api/models")
async def list_models(ultimate: bool = False):
    """保存済みモデルの一覧を取得"""
    try:
        if SUPABASE_ENABLED and get_supabase_client():
            from app_config import list_models_from_supabase  # type: ignore
            sb_models = list_models_from_supabase()
            sb_models = [m for m in sb_models if m.get("ultimate_mode", False) == ultimate]
            return {"models": sb_models, "count": len(sb_models)}

        models = []
        for model_path in MODELS_DIR.glob("model_*.joblib"):
            try:
                bundle = joblib.load(model_path)
                is_ultimate = bundle.get("ultimate_mode", False)
                if ultimate and not is_ultimate:
                    continue
                if not ultimate and is_ultimate:
                    continue
                models.append({
                    "model_id": model_path.stem,  # ファイルステムは必ずユニークなモデルID
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
        if SUPABASE_ENABLED and get_supabase_client():
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
