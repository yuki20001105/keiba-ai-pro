"""特徴量カタログ分析エンドポイント

GET /api/features/catalog    — 全カタログ JSON
GET /api/features/summary    — サマリー統計（カウント・ステージ別）
GET /api/features/importance — 学習済みモデルの特徴量重要度
GET /api/features/coverage   — カタログ特徴量のモデル内カバレッジ
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import joblib
from fastapi import APIRouter, HTTPException, Query

# keiba_ai パッケージへのパスを追加
_KEIBA_ROOT = Path(__file__).parent.parent.parent / "keiba"
if str(_KEIBA_ROOT) not in sys.path:
    sys.path.insert(0, str(_KEIBA_ROOT))

from keiba_ai.feature_catalog import FeatureCatalog  # type: ignore

from app_config import MODELS_DIR  # type: ignore

router = APIRouter(prefix="/api/features", tags=["features"])

_CATALOG: FeatureCatalog | None = None
_CATALOG_HASH: str | None = None


def _get_catalog() -> FeatureCatalog:
    global _CATALOG, _CATALOG_HASH
    catalog_path = _KEIBA_ROOT / "feature_catalog.yaml"
    if not catalog_path.exists():
        raise HTTPException(status_code=500, detail="feature_catalog.yaml が見つかりません")
    # ファイルの変更を検知して再ロード
    new_hash = str(catalog_path.stat().st_mtime)
    if _CATALOG is None or _CATALOG_HASH != new_hash:
        _CATALOG = FeatureCatalog.load(catalog_path)
        _CATALOG_HASH = new_hash
    return _CATALOG


def _find_latest_model(target: str) -> Path | None:
    """指定ターゲットの最新モデルを返す。"""
    candidates = sorted(MODELS_DIR.glob(f"model_{target}_*.joblib"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _build_desc_map(catalog: FeatureCatalog) -> dict[str, str]:
    """カタログから name→description の辞書を構築する。
    カタログ未登録の *_encoded 列は元カラム名の説明をもとに自動生成する。
    unnecessary_columns は reason を説明として補完する。
    """
    desc_map: dict[str, str] = {
        f["name"]: f.get("description", "")
        for f in catalog.engineered_features()
    }
    for category, fields in catalog.scraped_fields_with_descriptions().items():
        for field in fields:
            name = field["name"]
            if name not in desc_map:
                desc = field["description"] or f"スクレイプ列 ({category})"
                desc_map[name] = desc

    # unnecessary_columns: reason を説明として補完（モデルに残存する旧列用）
    for uc in catalog.unnecessary_columns_with_reasons():
        name = uc.get("name", "")
        reason = uc.get("reason", "")
        if name and not desc_map.get(name):
            desc_map[name] = f"【削除済み】{reason}" if reason else "【削除済み】"

    # *_encoded 列: 元カラム名の説明に「（ラベルエンコード）」を付与して自動生成
    encoded_additions: dict[str, str] = {}
    for col, base_desc in desc_map.items():
        encoded_col = f"{col}_encoded"
        if encoded_col not in desc_map:
            encoded_additions[encoded_col] = f"{base_desc}（ラベルエンコード）" if base_desc else f"{col} のラベルエンコード列"
    desc_map.update(encoded_additions)

    return desc_map


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/catalog")
def get_catalog() -> dict[str, Any]:
    """全カタログ内容を JSON として返す。"""
    catalog = _get_catalog()
    return {
        "version": catalog.version,
        "hash": catalog.hash(),
        "data": catalog.raw(),
    }


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """カタログのサマリー統計を返す。"""
    catalog = _get_catalog()
    return catalog.summary()


@router.get("/importance")
def get_importance(
    target: str = Query(default="win", description="モデルターゲット (win / place3 / speed_deviation)"),
    top_n: int = Query(default=50, ge=1, le=200, description="上位 N 件"),
    importance_type: str = Query(default="gain", description="重要度タイプ (gain / split)"),
) -> dict[str, Any]:
    """学習済みモデルから特徴量重要度を取得する。"""
    model_path = _find_latest_model(target)
    if model_path is None:
        raise HTTPException(status_code=404, detail=f"target='{target}' のモデルが見つかりません")

    try:
        bundle = joblib.load(model_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデルロードエラー: {e}")

    booster = bundle.get("model")
    feature_names: list[str] = (
        bundle.get("feature_columns")
        or bundle.get("feature_names")
        or []
    )

    if booster is None:
        raise HTTPException(status_code=500, detail="バンドルに 'model' キーがありません")

    # LightGBM Booster / sklearn wrapper 両対応
    try:
        if hasattr(booster, "feature_importance"):
            # lightgbm.Booster
            raw_imp = booster.feature_importance(importance_type=importance_type)
            names = booster.feature_name() if not feature_names else feature_names
        elif hasattr(booster, "booster_"):
            # sklearn LGBMClassifier/Regressor
            raw_imp = booster.booster_.feature_importance(importance_type=importance_type)
            names = booster.booster_.feature_name() if not feature_names else feature_names
        else:
            raise HTTPException(status_code=500, detail="LightGBM ブースターが見つかりません")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"feature_importance 取得エラー: {e}")

    total = float(raw_imp.sum()) or 1.0
    pairs = sorted(zip(names, raw_imp.tolist()), key=lambda x: x[1], reverse=True)
    top = pairs[:top_n]

    # カタログから説明を引く（name→description の辞書）
    catalog = _get_catalog()
    desc_map = _build_desc_map(catalog)

    return {
        "model_path": model_path.name,
        "target": target,
        "importance_type": importance_type,
        "total_features": len(names),
        "features": [
            {
                "name": name,
                "importance": imp,
                "importance_pct": round(imp / total * 100, 4),
                "description": desc_map.get(name, ""),
            }
            for name, imp in top
        ],
    }


@router.get("/coverage")
def get_coverage(
    target: str = Query(default="win", description="モデルターゲット"),
) -> dict[str, Any]:
    """カタログの enabled 特徴量がモデルに実際に含まれているかを検証する。"""
    catalog = _get_catalog()
    model_path = _find_latest_model(target)

    model_features: set[str] = set()
    model_name = "(モデルなし)"

    if model_path is not None:
        try:
            bundle = joblib.load(model_path)
            booster = bundle.get("model")
            # "feature_columns" は train.py が保存するキー名
            bundle_names: list[str] = (
                bundle.get("feature_columns")
                or bundle.get("feature_names")
                or []
            )
            if booster is not None and not bundle_names:
                if hasattr(booster, "feature_name"):
                    bundle_names = booster.feature_name()
                elif hasattr(booster, "booster_"):
                    bundle_names = booster.booster_.feature_name()
            model_features = set(bundle_names)
            model_name = model_path.name
        except Exception:
            pass

    enabled = catalog.enabled_features()
    disabled = catalog.disabled_features()

    in_model = [f for f in enabled if f in model_features]
    missing = [f for f in enabled if f not in model_features]
    extra = sorted(model_features - set(enabled) - set(disabled))

    desc_map = _build_desc_map(catalog)

    return {
        "model": model_name,
        "target": target,
        "catalog_enabled": len(enabled),
        "model_total": len(model_features),
        "matched": len(in_model),
        "missing_from_model": [{"name": f, "description": desc_map.get(f, "")} for f in missing],
        "extra_in_model": [{"name": f, "description": desc_map.get(f, "")} for f in extra],
    }


def _load_model_features(target: str) -> tuple[set[str], str]:
    """モデルバンドルから特徴量セットとモデル名を返す共通ヘルパー。"""
    model_path = _find_latest_model(target)
    if model_path is None:
        return set(), "(モデルなし)"
    try:
        bundle = joblib.load(model_path)
        booster = bundle.get("model")
        names: list[str] = bundle.get("feature_columns") or bundle.get("feature_names") or []
        if not names and booster is not None:
            if hasattr(booster, "feature_name"):
                names = booster.feature_name()
            elif hasattr(booster, "booster_"):
                names = booster.booster_.feature_name()
        return set(names), model_path.name
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"モデルロードエラー: {e}") from e


@router.get("/drift")
def get_drift(
    target: str = Query(default="win", description="モデルターゲット (win / place3 / speed_deviation)"),
) -> dict[str, Any]:
    """カタログとモデルの特徴量ドリフトを検出し、必要なアクションを返す。

    3-way 比較:
      - catalog_enabled : feature_catalog.yaml で enabled=true の特徴量
      - model_features  : モデルバンドルの feature_columns
      - needs_retrain   : catalog にあるがモデルに未反映 → 再学習が必要
      - needs_catalog   : モデルにあるがカタログ未登録  → catalog 更新が必要
    """
    catalog = _get_catalog()
    model_features, model_name = _load_model_features(target)
    catalog_enabled = set(catalog.enabled_features())
    desc_map = _build_desc_map(catalog)

    needs_retrain = sorted(catalog_enabled - model_features)   # catalog → model に未反映
    needs_catalog = sorted(model_features - catalog_enabled)   # model → catalog に未登録
    aligned = sorted(catalog_enabled & model_features)

    if needs_retrain and needs_catalog:
        status = "both"
    elif needs_retrain:
        status = "needs_retrain"
    elif needs_catalog:
        status = "needs_catalog"
    else:
        status = "ok"

    actions: list[str] = []
    if needs_retrain:
        actions.append(
            f"カタログに {len(needs_retrain)} 件の enabled 特徴量がモデルに未存在 → 「学習」ページでモデルを再学習してください"
        )
    if needs_catalog:
        actions.append(
            f"モデルに {len(needs_catalog)} 件のカタログ未登録特徴量 → keiba/feature_catalog.yaml の engineered_features に追記してください"
        )
    if not actions:
        actions.append("カタログとモデルの特徴量は整合しています")

    return {
        "status": status,
        "target": target,
        "model": model_name,
        "catalog_version": catalog.version,
        "catalog_hash": catalog.hash()[:12],
        "catalog_enabled_count": len(catalog_enabled),
        "model_feature_count": len(model_features),
        "aligned_count": len(aligned),
        "needs_retrain": [{"name": n, "description": desc_map.get(n, "")} for n in needs_retrain],
        "needs_catalog": [{"name": n, "description": desc_map.get(n, "")} for n in needs_catalog],
        "actions": actions,
    }


@router.get("/drift/all")
def get_drift_all() -> dict[str, Any]:
    """全ターゲット（win / place3 / speed_deviation）のドリフトサマリーを返す。"""
    targets = ["win", "place3", "speed_deviation"]
    catalog = _get_catalog()
    catalog_enabled = set(catalog.enabled_features())
    results = {}
    overall_ok = True

    for t in targets:
        try:
            model_features, model_name = _load_model_features(t)
        except HTTPException:
            results[t] = {"status": "model_error", "model": "(ロードエラー)"}
            overall_ok = False
            continue

        if not model_features:
            results[t] = {"status": "no_model", "model": "(モデルなし)"}
            continue

        nr = len(catalog_enabled - model_features)
        nc = len(model_features - catalog_enabled)
        if nr and nc:
            st = "both"
        elif nr:
            st = "needs_retrain"
        elif nc:
            st = "needs_catalog"
        else:
            st = "ok"

        if st != "ok":
            overall_ok = False

        results[t] = {
            "status": st,
            "model": model_name,
            "needs_retrain_count": nr,
            "needs_catalog_count": nc,
            "aligned_count": len(catalog_enabled & model_features),
        }

    return {
        "overall_status": "ok" if overall_ok else "drift",
        "catalog_version": catalog.version,
        "catalog_hash": catalog.hash()[:12],
        "catalog_enabled_count": len(catalog_enabled),
        "targets": results,
    }
