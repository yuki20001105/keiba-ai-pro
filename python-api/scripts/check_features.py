#!/usr/bin/env python3
"""特徴量整合性チェック CLI ツール

特徴量エンジニアリングを変更した後に実行して、
feature_catalog.yaml とモデルバンドルの整合性を確認する。

使用法:
    python python-api/scripts/check_features.py           # 全ターゲット確認
    python python-api/scripts/check_features.py --target win   # 特定ターゲットのみ
    python python-api/scripts/check_features.py --quiet   # 差分のみ表示

終了コード:
    0 = 全ターゲット整合
    1 = ドリフトあり（再学習 or カタログ更新が必要）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# パス設定
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "python-api"))
sys.path.insert(0, str(_ROOT / "keiba"))

import joblib
from keiba_ai.feature_catalog import FeatureCatalog  # type: ignore

_MODELS_DIR = _ROOT / "python-api" / "models"
_CATALOG_PATH = _ROOT / "keiba" / "feature_catalog.yaml"

TARGETS = ["win", "place3", "speed_deviation"]

STATUS_OK = "\033[32m✓ OK\033[0m"
STATUS_WARN = "\033[33m⚠ DRIFT\033[0m"
STATUS_ERR = "\033[31m✗ ERROR\033[0m"


def _find_latest_model(target: str) -> Path | None:
    candidates = sorted(
        _MODELS_DIR.glob(f"model_{target}_*.joblib"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def check_target(target: str, catalog_enabled: set[str], verbose: bool) -> bool:
    """True = OK / False = ドリフトあり"""
    model_path = _find_latest_model(target)
    if model_path is None:
        if verbose:
            print(f"  [{target}] \033[2m(モデルなし — スキップ)\033[0m")
        return True

    try:
        bundle = joblib.load(model_path)
    except Exception as e:
        print(f"  [{target}] {STATUS_ERR} — モデルロード失敗: {e}")
        return False

    model_features: set[str] = set(
        bundle.get("feature_columns") or bundle.get("feature_names") or []
    )
    if not model_features:
        booster = bundle.get("model")
        if booster is not None:
            if hasattr(booster, "feature_name"):
                model_features = set(booster.feature_name())
            elif hasattr(booster, "booster_"):
                model_features = set(booster.booster_.feature_name())

    needs_retrain = sorted(catalog_enabled - model_features)  # catalog → model に未反映
    needs_catalog = sorted(model_features - catalog_enabled)  # model → catalog に未登録

    if not needs_retrain and not needs_catalog:
        if verbose:
            print(f"  [{target}] {STATUS_OK} — {len(model_features)} 特徴量 ({model_path.name})")
        return True

    print(f"  [{target}] {STATUS_WARN} — {model_path.name}")
    if needs_retrain:
        print(f"    \033[33m→ 再学習が必要 ({len(needs_retrain)} 件): カタログに追加されたがモデル未反映\033[0m")
        for f in needs_retrain[:10]:
            print(f"        + {f}")
        if len(needs_retrain) > 10:
            print(f"        ... + {len(needs_retrain) - 10} 件")
    if needs_catalog:
        print(f"    \033[34m→ カタログ登録が必要 ({len(needs_catalog)} 件): モデルにあるがカタログ未登録\033[0m")
        for f in needs_catalog[:10]:
            print(f"        ? {f}")
        if len(needs_catalog) > 10:
            print(f"        ... + {len(needs_catalog) - 10} 件")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="特徴量整合性チェック — feature_catalog.yaml とモデルバンドルを比較"
    )
    parser.add_argument(
        "--target", choices=TARGETS, help="チェック対象ターゲット（省略=全て）"
    )
    parser.add_argument("--quiet", action="store_true", help="差分のみ表示（OK は非表示）")
    args = parser.parse_args()

    if not _CATALOG_PATH.exists():
        print(f"\033[31mERROR: feature_catalog.yaml が見つかりません: {_CATALOG_PATH}\033[0m")
        sys.exit(1)

    catalog = FeatureCatalog.load(_CATALOG_PATH)
    catalog_enabled = set(catalog.enabled_features())
    targets = [args.target] if args.target else TARGETS
    verbose = not args.quiet

    print("=" * 60)
    print("特徴量整合性チェック")
    print(f"  catalog : {_CATALOG_PATH.relative_to(_ROOT)}")
    print(f"  models  : {_MODELS_DIR.relative_to(_ROOT)}")
    print(f"  enabled : {len(catalog_enabled)} 特徴量 (v{catalog.version}, {catalog.hash()[:8]})")
    print("=" * 60)

    all_ok = True
    for t in targets:
        ok = check_target(t, catalog_enabled, verbose)
        if not ok:
            all_ok = False

    print("=" * 60)
    if all_ok:
        print(f"{STATUS_OK} — 全ターゲット整合")
    else:
        print(f"{STATUS_WARN} — ドリフトあり。以下の手順で修正してください:")
        print()
        print("  再学習が必要な場合 (needs_retrain):")
        print("    1. UI「学習」ページ または python-api/training/optimizer.py でモデルを再学習")
        print()
        print("  カタログ登録が必要な場合 (needs_catalog):")
        print("    1. keiba/feature_catalog.yaml の engineered_features セクションに追記")
        print("       例:")
        print("         - name: my_new_feature")
        print("           stage: derived")
        print("           dtype: float")
        print("           description: '説明'")
        print("           enabled: true")
        print()
        print("  再確認: python python-api/scripts/check_features.py")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
