# feature_inspection.ipynb → Notebook 分割 移行マップ

> `docs/features/feature_inspection.ipynb`（41セル）を各 Notebook に SRP 分割した記録。  
> 分割完了後 `feature_inspection.ipynb` は削除してよい。

---

## ① 移行マップ（セル→移行先）

| 元セル ID | 元セル内容 | 移行先 Notebook | セルラベル |
|---|---|---|---|
| Cell 1 | 環境構築・sys.path・import | — | 各 Notebook の bootstrap セルに統合済み |
| Cell 2 | DB ロード (`load_ultimate_training_frame`) | — | 各 Notebook のデータロードセルに統合済み |
| Cell 3 | `add_derived_features` 実行 | `02_data_validation` | FI-1 内でフォールバックロード |
| Cell 4 | FEATURE_GROUPS + ipywidgets ドロップダウン | `02_data_validation` | FI-1 |
| Cell 5 | 個別列インスペクション（ヒストグラム） | `02_data_validation` | FI-2 |
| Cell 6 | NaN率ランキングチャート（全特徴量） | `02_data_validation` | FI-3 |
| Cell 7 | `LightGBMFeatureOptimizer.fit_transform()` | `02_data_validation` | FI-4 |
| Cell 8 | 前処理前後の削除列比較テーブル | `02_data_validation` | FI-4 |
| Cell 9 | 前処理後サマリー | `02_data_validation` | FI-4 |
| Cell 10 | 前処理後 NaN率チャート | `02_data_validation` | FI-5 |
| Cell 11 | ストア初期化（FEATURE_STORE 等） | `notebooks/utils/nb_config.py` | 移行済み |
| Cell 12 | `_classify()` + missing_report_df 生成 | `02_data_validation` | FI-6 |
| Cell 13 | 断捨離レポート可視化 + CSV保存 | `02_data_validation` | FI-7 |
| Cell D | ターゲット生成 (`_make_target`) | `05_model_training` | TR-F 内の `_make_target_ts` |
| Cell E | LGB パラメータ設定 / USE_OPTUNA / TEST_DAYS | `05_model_training` | TR-E |
| Cell F | 時系列スプリット (cutoff = max_date - TEST_DAYS) | `05_model_training` | TR-F |
| Cell G | Optuna → `lgb.train()` 学習 | `05_model_training` | TR-G |
| Cell H | モデルバンドル保存 (MODEL_STORE + MODELS_DIR) | `05_model_training` | TR-H |
| Cell L1 | 品質評価 (NaN・外れ値・リーク疑い) | `04_feature_analysis` | L1 |
| Cell L2 | 単変量評価 (Spearman + MI) | `04_feature_analysis` | L2 |
| Cell L3 | 多重共線性 (Pearson + VIF) | `04_feature_analysis` | L3 |
| Cell L4 | モデルベース重要度 (Gain + Perm + SHAP) | `04_feature_analysis` | L4 |
| Cell L5 | グループ貢献度 | `04_feature_analysis` | L5 |
| Cell L6 | 統合ランキング → `feature_rank_report.csv` | `04_feature_analysis` | L6 |
| Cell I  | 剪定候補リスト生成 | `04_feature_analysis` | Cell I |
| Cell J  | 段階除去シミュレーション | `04_feature_analysis` | Cell J |
| Cell K  | 剪定可視化 + `feature_pruning_report.csv` | `04_feature_analysis` | Cell K |
| Cell OUT | `feature_analysis.json` → FEATURE_STORE | `04_feature_analysis` | OUT |
| Cell P_ROI | テストセット ROI 評価 → `roi_report.csv` | `07_evaluation` | P_ROI |
| Cell M1 | `feature_analysis_json` 構築 | `08_reporting` | M1 |
| Cell M2 | LLM プロンプト設定 | `08_reporting` | M2 |
| Cell M3 | LLM 呼び出し + `feature_llm_report.md` 保存 | `08_reporting` | M3 |
| Cell P_Ollama | Ollama 起動確認 | `08_reporting` | P_Ollama |
| Cell P_Notion | Notion ページエクスポート | `08_reporting` | P_Notion |
| Cells 40-41 | デバッグ print（high_corr_pairs 確認） | — | **削除**（移行しない） |

---

## ② Notebook 依存関係図

```
00_setup
    ↓
01_data_acquisition
    ↓
02_data_validation ─── (missing_report.csv)
    ↓
03_feature_engineering ─── FEATURE_STORE/feature_df（将来）
    ↓
04_feature_analysis ←── MODEL_STORE/lgb_model_{target}
    │                     (05 を先に実行)
    ├── feature_rank_report.csv
    ├── feature_pruning_report.csv
    └── FEATURE_STORE/feature_analysis.json
         ↓
05_model_training ─── MODEL_STORE/lgb_model_{target}
    │                  FEATURE_STORE/feature_importance_{target}
    └── MODELS_DIR/lgb_model_{target}.pkl (本番API参照)
         ↓
06_prediction
    ↓
07_evaluation ─── REPORT_STORE/roi_stats, roi_df
    │              roi_report.csv
    └─────────────────────────────────────────┐
                                              ↓
08_reporting ←── FEATURE_STORE/feature_analysis.json
             ←── REPORT_STORE/roi_stats
             ←── MODEL_STORE/lgb_model_{target}
             └── REPORTS_DIR/feature_llm_report.md
```

---

## ③ 実行順序

```
推奨実行順序:
1. 00_setup.ipynb           — 環境確認
2. 01_data_acquisition.ipynb — (必要時) DB 構築
3. 02_data_validation.ipynb  — データ品質 + 欠損レポート
4. 03_feature_engineering.ipynb — 特徴量生成 + feature_df 保存
5. 05_model_training.ipynb   — 時系列スプリット + 学習 → MODEL_STORE
6. 04_feature_analysis.ipynb — 特徴量ランキング + 剪定シミュレーション
7. 07_evaluation.ipynb       — AUC/ROI/NDCG 評価
8. 08_reporting.ipynb        — LLM レポート + Notion 出力

※ 04 は 05 の後に実行 (MODEL_STORE が必要)
※ 06_prediction.ipynb は 05 の後 (本番 API と並列運用可)
```

---

## ④ Notebook 担当責任一覧

| Notebook | 主な責任 | 主な出力 |
|---|---|---|
| `00_setup` | 環境・DB・依存確認 | — |
| `01_data_acquisition` | netkeiba スクレイプ・DB 構築 | keiba_ultimate.db |
| `02_data_validation` | データ品質・欠損・前処理前後比較 | `missing_report.csv`, `nan_rate_ranking.png` |
| `03_feature_engineering` | `add_derived_features` + POST_RACE 除外 + feature 保存 | FEATURE_STORE/feature_df |
| `04_feature_analysis` | 7段階特徴量ランキング + 剪定シミュレーション | `feature_rank_report.csv`, `feature_pruning_report.csv`, FEATURE_STORE/feature_analysis.json |
| `05_model_training` | 時系列スプリット + Optuna + lgb.train | MODEL_STORE/lgb_model_{target}.pkl |
| `06_prediction` | 新規レース予測 + Kelly 推奨 | prediction_df |
| `07_evaluation` | AUC/ROI/NDCG 評価 + 累積 ROI | `roi_report.csv`, REPORT_STORE/roi_stats |
| `08_reporting` | LLM レポート生成 + Notion 出力 | `feature_llm_report.md` |

---

## ⑤ 整理後のディレクトリ構造

```
notebooks/
├── utils/
│   ├── __init__.py
│   └── nb_config.py            ← 全 Notebook 共通設定・ストア I/O
├── data/
│   ├── feature_store/          ← 03→04/05/06 データ受け渡し
│   │   ├── feature_analysis.json
│   │   └── feature_importance_{target}.pkl
│   ├── model_store/            ← 05→04/06/07 モデル受け渡し
│   │   └── lgb_model_{target}.pkl
│   └── report_store/           ← 07/08 中間出力
│       ├── roi_stats.pkl
│       ├── roi_df.pkl
│       └── final_rank_df.pkl
├── reports/                    ← 最終 CSV・PNG 出力
│   ├── missing_report.csv
│   ├── feature_rank_report.csv
│   ├── feature_pruning_report.csv
│   ├── roi_report.csv
│   ├── feature_llm_report.md
│   └── *.png
├── 00_setup.ipynb
├── 01_data_acquisition.ipynb
├── 02_data_validation.ipynb
├── 03_feature_engineering.ipynb
├── 04_feature_analysis.ipynb
├── 05_model_training.ipynb
├── 06_prediction.ipynb
├── 07_evaluation.ipynb
└── 08_reporting.ipynb
```

---

## ⑥ 共通ユーティリティ候補

| 関数/定数 | 現在の場所 | 統一先 |
|---|---|---|
| `save_store()` / `load_store()` | `nb_config.py` | 完了 |
| `FEATURE_GROUPS` 定義 | `02` FI-1 / `04` L5 に重複 | → `nb_config.py` または `utils/feature_groups.py` に統一推奨 |
| `_make_target()` | `05` TR-F 内 / `04` L0 内 に重複 | → `utils/nb_utils.py` に切り出し推奨 |
| `_task_type` 判定ロジック | `05` TR-G / `04` L0 / `07` P_ROI | → `nb_config.py` の `TARGET_TASK_MAP` で統一推奨 |
| `LightGBMFeatureOptimizer.fit_transform()` 呼び出しパターン | `02/04/05` に重複 | → ラッパー関数化推奨 |

---

## ⑦ 重複コード削除リスト

| 重複内容 | 出現箇所 | 対応 |
|---|---|---|
| `FEATURE_GROUPS` 辞書（13グループ） | `02` FI-1 / `04` L5 | 今後 `nb_config.py` に移動 |
| `_make_target` 関数 | `05` TR-F / `04` L0 | 今後 `utils/nb_utils.py` に切り出し |
| `LightGBMFeatureOptimizer` + `fit_transform` パターン | `02` FI-4 / `04` L0 / `05` TR-F | 今後ラッパー化 |
| `roc_auc_score` / `mean_squared_error` インポート | `05` TR-G / `04` Cell J | 各 Notebook 独立で問題なし |

---

## ⑧ 移行後の各 Notebook 完全セル構成

### 02_data_validation.ipynb（Section 7-8 追加分）

| セル | タイプ | 内容 |
|---|---|---|
| … (既存 1-8) | — | bootstrap〜report |
| Section 7 header | Markdown | 特徴量グループ別インスペクション |
| FI-1 | Code | FEATURE_GROUPS + ipywidgets ドロップダウン |
| FI-2 | Code | 個別列インスペクション + ヒストグラム |
| FI-3 | Code | NaN率ランキングチャート |
| FI-4 | Code | LightGBMFeatureOptimizer 前処理前後比較 |
| FI-5 | Code | 前処理後 NaN率チャート |
| Section 8 header | Markdown | 欠損値断捨離レポート |
| FI-6 | Code | `_classify()` + missing_report_df |
| FI-7 | Code | 可視化 + missing_report.csv 保存 |

### 04_feature_analysis.ipynb（Section 8-9 追加分）

| セル | タイプ | 内容 |
|---|---|---|
| … (既存 1-10) | — | bootstrap〜feature_importance.csv |
| Section 8 header | Markdown | 7段階ランキング説明 |
| L0 | Code | MODEL_STORE バンドルロード + テストセット再構築 |
| L1 | Code | 品質評価 (NaN・外れ値・リーク) |
| L2 | Code | Spearman + 相互情報量 |
| L3 | Code | 高相関ペア + VIF |
| L4 | Code | Gain + Permutation + SHAP |
| L5 | Code | グループ貢献度 |
| L6 | Code | 統合ランキング + feature_rank_report.csv |
| Section 9 header | Markdown | 剪定シミュレーション説明 |
| Cell I | Code | 剪定候補リスト生成 |
| Cell J | Code | 段階除去シミュレーション |
| Cell K | Code | 可視化 + feature_pruning_report.csv |
| OUT | Code | feature_analysis.json → FEATURE_STORE |

### 05_model_training.ipynb（Section 6 追加分）

| セル | タイプ | 内容 |
|---|---|---|
| … (既存 1-6) | — | bootstrap〜モデル保存 |
| Section 6 header | Markdown | 時系列スプリット説明 |
| TR-E | Code | パラメータ設定 |
| TR-F | Code | 時系列スプリット + 前処理 |
| TR-G | Code | Optuna → lgb.train() |
| TR-H | Code | モデルバンドル保存 + FEATURE_STORE |

### 07_evaluation.ipynb（Section 9 追加分）

| セル | タイプ | 内容 |
|---|---|---|
| … (既存 1-8) | — | bootstrap〜eval report |
| Section 9 header | Markdown | ROI 実戦評価説明 |
| P_ROI | Code | ROI 計算 + roi_report.csv + 累積 ROI グラフ |

### 08_reporting.ipynb（Section 6 追加分）

| セル | タイプ | 内容 |
|---|---|---|
| … (既存 1-5) | — | bootstrap〜HTML report |
| Section 6 header | Markdown | LLM レポート説明 |
| P_Ollama | Code | Ollama/OpenAI 接続確認 |
| M1 | Code | feature_analysis_json 構築 |
| M2 | Code | LLM プロンプト設定 |
| M3 | Code | LLM 呼び出し + feature_llm_report.md |
| P_Notion | Code | Notion エクスポート（ENABLE_NOTION=False） |
