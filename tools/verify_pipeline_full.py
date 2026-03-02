"""
フルパイプライン検証スクリプト
データ取得 → 特徴量エンジニアリング → モデル予測 の各ステップを実行し、
各段階の入出力データを CSV / JSON で保存する。

Usage:
    python-api/.venv/Scripts/python.exe tools/verify_pipeline_full.py [RACE_ID]

RACE_ID を省略した場合は DB 内の最新 races_ultimate から1件自動選択する。
"""
import sys
import json
import sqlite3
import traceback
from pathlib import Path

import sys, os

# ── パス設定 ─────────────────────────────────────────
ROOT = Path(__file__).parent.parent
PYAPI = ROOT / "python-api"
KEIBA = ROOT / "keiba"
sys.path.insert(0, str(PYAPI))
sys.path.insert(0, str(KEIBA))
os.chdir(PYAPI)   # app_config.py が相対パスで models/ 等を解決するため

# Windows CP932 端末でも UTF-8 出力
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

SECTION = lambda t: print(f"\n{'='*60}\n  {t}\n{'='*60}")

# ── 出力ディレクトリ ─────────────────────────────────
OUT_DIR = ROOT / "tools" / "pipeline_output"
OUT_DIR.mkdir(exist_ok=True)

RESULTS = {}   # ステップごとの検証結果を蓄積

# ===================================================================
# Step0: インポート確認
# ===================================================================
SECTION("Step 0: インポート確認")
try:
    import pandas as pd
    import numpy as np
    import joblib
    print("  ✓ pandas / numpy / joblib")
except ImportError as e:
    print(f"  ✗ {e}")
    sys.exit(1)

try:
    import lightgbm as lgb
    print(f"  ✓ lightgbm {lgb.__version__}")
except ImportError as e:
    print(f"  ✗ lightgbm: {e}")
    sys.exit(1)

try:
    from app_config import ULTIMATE_DB, MODELS_DIR, get_latest_model, load_model_bundle  # type: ignore
    print(f"  ✓ app_config")
    print(f"    ULTIMATE_DB : {ULTIMATE_DB}")
    print(f"    MODELS_DIR  : {MODELS_DIR}")
    RESULTS["db_exists"] = ULTIMATE_DB.exists()
    RESULTS["models_count"] = len(list(MODELS_DIR.glob("model_*.joblib")))
except Exception as e:
    print(f"  ✗ app_config: {e}")
    traceback.print_exc()
    sys.exit(1)

try:
    from keiba_ai.db_ultimate_loader import load_ultimate_training_frame  # type: ignore
    from keiba_ai.feature_engineering import add_derived_features  # type: ignore
    from keiba_ai.ultimate_features import UltimateFeatureCalculator  # type: ignore
    from keiba_ai.lightgbm_feature_optimizer import prepare_for_lightgbm_ultimate  # type: ignore
    print("  ✓ keiba_ai モジュール全て")
except Exception as e:
    print(f"  ✗ keiba_ai: {e}")
    traceback.print_exc()
    sys.exit(1)


# ===================================================================
# Step1: DB からデータ読み込み
# ===================================================================
SECTION("Step 1: DB データ読み込み (load_ultimate_training_frame)")
df_raw = load_ultimate_training_frame(ULTIMATE_DB)
if df_raw.empty:
    print("  ✗ データが空です。DB を確認してください。")
    sys.exit(1)

print(f"\n  行数  : {len(df_raw):,}")
print(f"  列数  : {len(df_raw.columns)}")
print(f"  レース数: {df_raw['race_id'].nunique() if 'race_id' in df_raw.columns else '?'}")

raw_cols = df_raw.columns.tolist()
print(f"\n  【生データ カラム一覧 ({len(raw_cols)}件)】")
for i in range(0, len(raw_cols), 6):
    print("    " + "  ".join(f"{c:<22}" for c in raw_cols[i:i+6]))

# 欠損率上位10件
null_rates = (df_raw.isnull().sum() / len(df_raw) * 100).sort_values(ascending=False)
print(f"\n  【欠損率上位10フィールド】")
for col, rate in null_rates.head(10).items():
    mark = "✗" if rate > 30 else ("△" if rate > 5 else "✓")
    print(f"    {mark} {col:<35}: {rate:5.1f}%")

# 生データを CSV 保存（先頭200行）
raw_csv = OUT_DIR / "01_raw_data.csv"
df_raw.head(200).to_csv(raw_csv, index=False, encoding="utf-8-sig")
print(f"\n  → 生データ (先頭200行) を保存: {raw_csv}")
RESULTS["raw_rows"] = len(df_raw)
RESULTS["raw_cols"] = len(df_raw.columns)


# ===================================================================
# Step2: 特徴量エンジニアリング (add_derived_features)
# ===================================================================
SECTION("Step 2: 派生特徴量追加 (add_derived_features)")
try:
    df_fe = add_derived_features(df_raw.copy(), full_history_df=df_raw.copy())
    new_cols = [c for c in df_fe.columns if c not in df_raw.columns]
    print(f"  元カラム数  : {len(df_raw.columns)}")
    print(f"  追加カラム数: {len(new_cols)}")
    print(f"  合計カラム数: {len(df_fe.columns)}")
    print(f"\n  【add_derived_features で追加されたカラム】")
    for i in range(0, len(new_cols), 4):
        print("    " + "  ".join(f"{c:<30}" for c in new_cols[i:i+4]))
    RESULTS["fe_added_cols"] = len(new_cols)
    RESULTS["fe_step"] = "OK"
except Exception as e:
    print(f"  ✗ add_derived_features 失敗: {e}")
    traceback.print_exc()
    df_fe = df_raw.copy()
    RESULTS["fe_step"] = f"ERROR: {e}"


# ===================================================================
# Step3: Ultimate 特徴量追加 (UltimateFeatureCalculator)
# ===================================================================
SECTION("Step 3: Ultimate特徴量追加 (UltimateFeatureCalculator)")
try:
    calculator = UltimateFeatureCalculator(str(ULTIMATE_DB))
    df_ult = calculator.add_ultimate_features(df_fe.copy())
    df_ult = df_ult.loc[:, ~df_ult.columns.duplicated()]
    ult_new = [c for c in df_ult.columns if c not in df_fe.columns]
    print(f"  add_derived_features 後のカラム数: {len(df_fe.columns)}")
    print(f"  追加カラム数: {len(ult_new)}")
    print(f"  合計カラム数: {len(df_ult.columns)}")
    print(f"\n  【UltimateFeatureCalculator で追加されたカラム】")
    for i in range(0, len(ult_new), 4):
        print("    " + "  ".join(f"{c:<35}" for c in ult_new[i:i+4]))
    RESULTS["ult_added_cols"] = len(ult_new)
    RESULTS["ult_step"] = "OK"
except Exception as e:
    print(f"  ✗ UltimateFeatureCalculator 失敗: {e}")
    traceback.print_exc()
    df_ult = df_fe.copy()
    RESULTS["ult_step"] = f"ERROR: {e}"


# ===================================================================
# Step4: LightGBM 特徴量最適化 (prepare_for_lightgbm_ultimate)
# ===================================================================
SECTION("Step 4: LightGBM特徴量最適化 (prepare_for_lightgbm_ultimate)")
try:
    # モデルバンドルが既にある場合はそのオプティマイザーを使う（推論時は必須）
    # Step5 より前なので、まずモデルバンドルを仮ロードしてオプティマイザー取得
    _pre_model_path = get_latest_model()
    if _pre_model_path is None:
        _ult_models = sorted(MODELS_DIR.glob("model_*_ultimate.joblib"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
        _pre_model_path = _ult_models[0] if _ult_models else None
    if _pre_model_path:
        _pre_bundle = load_model_bundle(_pre_model_path)
        _pre_optimizer = _pre_bundle.get("optimizer")
        if _pre_optimizer:
            df_lgb = _pre_optimizer.transform(df_ult.copy())
            categorical_features = _pre_bundle.get("categorical_features", [])
        else:
            # optimizer なし旧バンドル → 学習モードで fit_transform
            df_lgb, _pre_optimizer, categorical_features = prepare_for_lightgbm_ultimate(
                df_ult.copy(), target_col="win", is_training=True
            )
    else:
        df_lgb, _pre_optimizer, categorical_features = prepare_for_lightgbm_ultimate(
            df_ult.copy(), target_col="win", is_training=True
        )
    exclude_meta = {"race_id", "horse_id", "jockey_id", "trainer_id", "owner_id",
                    "finish_position", "finish", "win", "place3"}
    X_cols = [c for c in df_lgb.columns if c not in exclude_meta
              and df_lgb[c].dtype != object]
    print(f"  最適化後カラム数: {len(df_lgb.columns)}")
    print(f"  AI 入力特徴量数  : {len(X_cols)}")
    print(f"  カテゴリカル特徴量 ({len(categorical_features)}件): {categorical_features}")
    print(f"\n  【AI 入力特徴量カラム一覧】")
    for i in range(0, len(X_cols), 4):
        print("    " + "  ".join(f"{c:<35}" for c in X_cols[i:i+4]))

    # 特徴量エンジニアリング後のデータを CSV 保存（先頭200行）
    fe_csv = OUT_DIR / "02_feature_engineered.csv"
    df_lgb[X_cols].head(200).to_csv(fe_csv, index=False, encoding="utf-8-sig")
    print(f"\n  → 特徴量エンジニアリング済みデータ (先頭200行) を保存: {fe_csv}")

    # 特徴量の要約統計量
    stats_csv = OUT_DIR / "02b_feature_stats.csv"
    df_lgb[X_cols].describe().T.to_csv(stats_csv, encoding="utf-8-sig")
    print(f"  → 特徴量統計量を保存: {stats_csv}")

    RESULTS["lgb_feature_cols"] = len(X_cols)
    RESULTS["lgb_step"] = "OK"
except Exception as e:
    print(f"  ✗ prepare_for_lightgbm_ultimate 失敗: {e}")
    traceback.print_exc()
    X_cols = []
    RESULTS["lgb_step"] = f"ERROR: {e}"


# ===================================================================
# Step5: モデル読み込み
# ===================================================================
SECTION("Step 5: モデル読み込み")
model_path = get_latest_model()

if model_path is None:
    # ultimate サフィックスのものを探す
    ultimate_models = sorted(
        MODELS_DIR.glob("model_*_ultimate.joblib"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    model_path = ultimate_models[0] if ultimate_models else None

if model_path is None:
    print("  ✗ モデルファイルが見つかりません")
    RESULTS["model_step"] = "NO MODEL"
else:
    try:
        bundle = load_model_bundle(model_path)
        model = bundle["model"]
        saved_features = bundle.get("feature_columns", [])
        metrics = bundle.get("metrics", {})
        print(f"  ✓ モデル読み込み成功")
        print(f"    ファイル    : {model_path.name}")
        print(f"    モデル種別  : {bundle.get('model_type', '?')}")
        print(f"    学習日付範囲: {bundle.get('training_date_from')} ~ {bundle.get('training_date_to')}")
        print(f"    学習データ数: {bundle.get('data_count', '?'):,} 行 / {bundle.get('race_count', '?')} レース")
        print(f"    保存特徴量数: {len(saved_features)}")
        print(f"    AUC       : {metrics.get('auc', '?'):.4f}")
        print(f"    LogLoss   : {metrics.get('logloss', '?'):.4f}")
        print(f"    CV AUC    : {metrics.get('cv_auc_mean', '?'):.4f} ± {metrics.get('cv_auc_std', '?'):.4f}")

        # 特徴量重要度を CSV 保存
        try:
            if hasattr(model, "feature_importance"):
                fi = model.feature_importance(importance_type="gain")
                fn = model.feature_name() if hasattr(model, "feature_name") else saved_features
                fi_df = pd.DataFrame({"feature": fn[:len(fi)], "importance_gain": fi}).sort_values(
                    "importance_gain", ascending=False
                )
                fi_csv = OUT_DIR / "03_feature_importance.csv"
                fi_df.to_csv(fi_csv, index=False, encoding="utf-8-sig")
                print(f"\n  【特徴量重要度 Top20】")
                for _, row in fi_df.head(20).iterrows():
                    bar = "█" * int(row["importance_gain"] / fi_df["importance_gain"].max() * 30)
                    print(f"    {row['feature']:<40} {row['importance_gain']:>10.1f}  {bar}")
                print(f"\n  → 特徴量重要度を保存: {fi_csv}")
        except Exception as e_fi:
            print(f"  ⚠ 特徴量重要度取得失敗: {e_fi}")

        RESULTS["model_step"] = "OK"
        RESULTS["model_auc"] = metrics.get("auc")
    except Exception as e:
        print(f"  ✗ モデル読み込み失敗: {e}")
        traceback.print_exc()
        bundle = None
        RESULTS["model_step"] = f"ERROR: {e}"
        model_path = None


# ===================================================================
# Step6: サンプルレースで予測実行
# ===================================================================
SECTION("Step 6: サンプルレースで予測実行")

# レースID の決定 (コマンドライン引数 or DB 自動選択)
target_race_id = sys.argv[1] if len(sys.argv) > 1 else None

if target_race_id is None:
    conn = sqlite3.connect(str(ULTIMATE_DB))
    cur = conn.cursor()
    # 頭数が多いレースを選ぶ（最新から）
    cur.execute("""
        SELECT r.race_id, COUNT(*) AS cnt
        FROM races_ultimate r
        JOIN race_results_ultimate h ON h.race_id = r.race_id
        GROUP BY r.race_id
        ORDER BY r.race_id DESC
        LIMIT 100
    """)
    rows = cur.fetchall()
    conn.close()
    best = max(rows, key=lambda x: x[1]) if rows else None
    target_race_id = best[0] if best else None
    if target_race_id:
        print(f"  自動選択レース: {target_race_id} ({best[1]}頭)")
    else:
        print("  ✗ 対象レースが見つかりません")
        RESULTS["pred_step"] = "NO RACE"

if target_race_id and model_path:
    try:
        # レースデータ取得
        conn = sqlite3.connect(str(ULTIMATE_DB))
        cur = conn.cursor()
        cur.execute("SELECT data FROM races_ultimate WHERE race_id = ?", (target_race_id,))
        race_row = cur.fetchone()
        cur.execute(
            "SELECT data FROM race_results_ultimate WHERE race_id = ? ORDER BY CAST(json_extract(data, '$.horse_number') AS INTEGER)",
            (target_race_id,)
        )
        horse_rows = cur.fetchall()
        conn.close()

        if not race_row or not horse_rows:
            print(f"  ✗ レース {target_race_id} のデータが DB に見つかりません")
            RESULTS["pred_step"] = "NO DATA"
        else:
            race_data = json.loads(race_row[0])
            print(f"  レース情報:")
            print(f"    レースID   : {target_race_id}")
            print(f"    レース名   : {race_data.get('race_name', '?')}")
            print(f"    会場       : {race_data.get('venue', '?')}")
            print(f"    日付       : {race_data.get('date', '?')}")
            print(f"    距離       : {race_data.get('distance', '?')}m")
            print(f"    トラック   : {race_data.get('track_type', '?')}")
            print(f"    頭数       : {len(horse_rows)}")

            # 馬データを DataFrame に変換
            horse_records = []
            for hr in horse_rows:
                hd = json.loads(hr[0])
                hd["race_id"] = target_race_id
                for k, v in race_data.items():
                    if k not in hd or hd[k] is None:
                        hd[k] = v
                horse_records.append(hd)

            df_pred = pd.DataFrame(horse_records)

            # カラム名正規化
            col_map = {
                "finish_position": "finish", "finish_time": "time",
                "track_type": "surface", "last_3f": "last_3f_time", "weight_kg": "horse_weight",
            }
            for old, new in col_map.items():
                if old in df_pred.columns and new not in df_pred.columns:
                    df_pred[new] = df_pred[old]

            # 数値変換
            for c in ["bracket_number", "horse_number", "odds", "popularity", "horse_weight",
                      "age", "distance", "num_horses", "horse_total_runs", "horse_total_wins"]:
                if c in df_pred.columns:
                    df_pred[c] = pd.to_numeric(df_pred[c], errors="coerce")

            # 性別・年齢パース
            if "sex_age" in df_pred.columns:
                if "sex" not in df_pred.columns or df_pred["sex"].isna().all():
                    df_pred["sex"] = df_pred["sex_age"].str.extract(r"^([牡牝セ])")[0]
                if "age" not in df_pred.columns or df_pred["age"].isna().all():
                    df_pred["age"] = pd.to_numeric(df_pred["sex_age"].str.extract(r"(\d+)$")[0], errors="coerce")

            # 生データを保存
            raw_pred_csv = OUT_DIR / f"04_prediction_raw_{target_race_id}.csv"
            df_pred.to_csv(raw_pred_csv, index=False, encoding="utf-8-sig")
            print(f"\n  → 予測入力 生データを保存: {raw_pred_csv}")

            # 特徴量エンジニアリング
            df_pred = add_derived_features(df_pred, full_history_df=df_pred)
            calc = UltimateFeatureCalculator(str(ULTIMATE_DB))
            df_pred = calc.add_ultimate_features(df_pred)
            df_pred = df_pred.loc[:, ~df_pred.columns.duplicated()]

            # LightGBM 向け変換
            bundle2 = load_model_bundle(model_path)
            model2 = bundle2["model"]
            bundle_optimizer = bundle2.get("optimizer")
            if bundle_optimizer:
                df_pred_opt = bundle_optimizer.transform(df_pred)
            else:
                df_pred_opt, _, _ = prepare_for_lightgbm_ultimate(df_pred, is_training=False)

            # 特徴量エンジニアリング済み予測データを保存
            fe_pred_csv = OUT_DIR / f"05_prediction_features_{target_race_id}.csv"
            df_pred_opt.to_csv(fe_pred_csv, index=False, encoding="utf-8-sig")
            print(f"  → 予測入力 特徴量エンジニアリング済みデータを保存: {fe_pred_csv}")

            # 特徴量を model に合わせて整形
            exclude_meta = {"win", "place3", "race_id", "horse_id", "jockey_id",
                            "trainer_id", "owner_id", "finish_position", "finish"}
            X_pred = df_pred_opt.drop([c for c in exclude_meta if c in df_pred_opt.columns], axis=1)
            X_pred = X_pred.select_dtypes(exclude=["object"])

            trained_features = (
                model2.feature_name() if hasattr(model2, "feature_name")
                else bundle2.get("feature_columns", list(X_pred.columns))
            )
            for mf in [f for f in trained_features if f not in X_pred.columns]:
                X_pred[mf] = 0.0
            X_pred = X_pred[[f for f in trained_features if f in X_pred.columns]]
            X_pred = X_pred[trained_features]

            print(f"\n  モデル入力特徴量数: {len(X_pred.columns)}")
            win_probs = model2.predict(X_pred)

            # 予測結果組み立て
            preds = []
            for i, rec in enumerate(horse_records):
                preds.append({
                    "horse_number": int(rec.get("horse_number", i+1)),
                    "horse_name": str(rec.get("horse_name", f"不明{i+1}")),
                    "odds": float(rec.get("odds") or 0),
                    "actual_finish": rec.get("finish_position") or rec.get("finish"),
                    "win_probability": float(win_probs[i]),
                })
            preds.sort(key=lambda x: x["win_probability"], reverse=True)
            for rank, p in enumerate(preds, 1):
                p["predicted_rank"] = rank

            print(f"\n  【予測結果】")
            print(f"    {'予測順':>4}  {'馬番':>4}  {'馬名':<16}  {'勝率':>7}  {'オッズ':>7}  {'実際着順':>6}")
            print(f"    {'-'*60}")
            for p in preds:
                print(f"    {p['predicted_rank']:>4}  {p['horse_number']:>4}  {p['horse_name']:<16}  "
                      f"{p['win_probability']:>6.1%}  {p['odds']:>7.1f}  {str(p['actual_finish']):>6}")

            # 予測結果を JSON 保存
            pred_json = OUT_DIR / f"06_predictions_{target_race_id}.json"
            with open(pred_json, "w", encoding="utf-8") as f:
                json.dump({
                    "race_id": target_race_id,
                    "race_name": race_data.get("race_name", ""),
                    "venue": race_data.get("venue", ""),
                    "date": race_data.get("date", ""),
                    "distance": race_data.get("distance", 0),
                    "predictions": preds,
                }, f, ensure_ascii=False, indent=2)
            print(f"\n  → 予測結果を保存: {pred_json}")

            # 予測精度 (実際のデータが存在する場合のみ)
            actual_finishes = {p["horse_number"]: p["actual_finish"] for p in preds}
            if any(v and str(v).isdigit() and int(str(v)) == 1 for v in actual_finishes.values()):
                actual_winner = next(k for k, v in actual_finishes.items()
                                     if v and str(v).isdigit() and int(str(v)) == 1)
                predicted_winner_num = preds[0]["horse_number"]
                hit_win = actual_winner == predicted_winner_num
                # 3着以内的中
                actual_top3 = {k for k, v in actual_finishes.items()
                               if v and str(v).isdigit() and int(str(v)) <= 3}
                predicted_top3 = {p["horse_number"] for p in preds[:3]}
                hit_place = bool(actual_top3 & predicted_top3)
                print(f"\n  【予測精度 (この1レース)】")
                print(f"    実際の1着  : {actual_winner}番")
                print(f"    予測1位     : {predicted_winner_num}番")
                print(f"    単勝的中   : {'✓ 的中!' if hit_win else '✗ 外れ'}")
                print(f"    複勝的中(3着以内 Top3予測中): {'✓ 的中!' if hit_place else '✗ 外れ'}")
                RESULTS["sample_win_hit"] = hit_win
                RESULTS["sample_place_hit"] = hit_place

            RESULTS["pred_step"] = "OK"
            RESULTS["pred_race_id"] = target_race_id
            RESULTS["pred_horses"] = len(preds)

    except Exception as e:
        print(f"  ✗ 予測実行失敗: {e}")
        traceback.print_exc()
        RESULTS["pred_step"] = f"ERROR: {e}"

elif not model_path:
    print("  スキップ (モデルなし)")


# ===================================================================
# Step7: 最終サマリー
# ===================================================================
SECTION("最終サマリー")
step_map = {
    "Step0 インポート": "OK (全モジュール正常)",
    "Step1 DB読み込み": f"OK ({RESULTS.get('raw_rows', '?'):,} 行 / {RESULTS.get('raw_cols', '?')} 列)",
    "Step2 add_derived_features": f"{RESULTS.get('fe_step', '?')} (+{RESULTS.get('fe_added_cols', '?')} 列)",
    "Step3 UltimateFeatureCalculator": f"{RESULTS.get('ult_step', '?')} (+{RESULTS.get('ult_added_cols', '?')} 列)",
    "Step4 LightGBM最適化": f"{RESULTS.get('lgb_step', '?')} ({RESULTS.get('lgb_feature_cols', '?')} 特徴量)",
    "Step5 モデル読み込み": f"{RESULTS.get('model_step', '?')} AUC={RESULTS.get('model_auc', '?')}",
    "Step6 予測実行": f"{RESULTS.get('pred_step', '?')} レース={RESULTS.get('pred_race_id', '?')} 頭数={RESULTS.get('pred_horses', '?')}",
}
for step, result in step_map.items():
    mark = "✓" if result.startswith("OK") else "✗"
    print(f"  {mark} {step:<35}: {result}")

print(f"\n  出力ファイル: {OUT_DIR}")
print("  ├ 01_raw_data.csv                    ← 生データ (先頭200行)")
print("  ├ 02_feature_engineered.csv          ← 特徴量エンジニアリング済みデータ")
print("  ├ 02b_feature_stats.csv              ← 特徴量統計量 (mean/std/min/max)")
print("  ├ 03_feature_importance.csv          ← 特徴量重要度")
print(f"  ├ 04_prediction_raw_<race_id>.csv   ← 予測入力生データ")
print(f"  ├ 05_prediction_features_<race_id>.csv ← 予測入力特徴量")
print(f"  └ 06_predictions_<race_id>.json     ← 予測結果")
print()
