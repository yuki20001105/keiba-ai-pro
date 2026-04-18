# 実装フロー詳細ドキュメント

> 最終更新: 2026-04-11

---

## 目次

1. [全体アーキテクチャ](#1-全体アーキテクチャ)
2. [データ取得フロー（スクレイピング）](#2-データ取得フロースクレイピング)
3. [データ読み込み・前処理フロー](#3-データ読み込み前処理フロー)
4. [特徴量エンジニアリングフロー](#4-特徴量エンジニアリングフロー)
5. [モデル学習フロー](#5-モデル学習フロー)
6. [予測フロー](#6-予測フロー)
7. [API エンドポイント一覧](#7-api-エンドポイント一覧)
8. [フロントエンド構成](#8-フロントエンド構成)
9. [データモデル（DB スキーマ概要）](#9-データモデルdb-スキーマ概要)

---

## 1. 全体アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│                  フロントエンド (Next.js)                 │
│  /data-collection → /train → /predict-batch              │
│          → /race-analysis → /dashboard                   │
└─────────────────────────┬────────────────────────────────┘
                          │ REST (fetch /api/*)
┌─────────────────────────▼────────────────────────────────┐
│             FastAPI バックエンド (python-api/main.py)     │
│  routers/: scrape │ train │ predict │ models_mgmt        │
│            races  │ stats │ export  │ purchase │ debug    │
└────┬────────────────────────────────────┬────────────────┘
     │ スクレイピング                       │ ML パイプライン
     ▼                                    ▼
netkeiba.com                  keiba/keiba_ai/
                              ├─ db_ultimate_loader.py
                              ├─ feature_engineering.py
                              ├─ train.py
                              └─ predict.py
     │                                    │
     ▼                                    ▼
keiba_ultimate.db (SQLite)        models/*.joblib
```

**技術スタック**
| レイヤー | 技術 |
|---|---|
| フロントエンド | Next.js 14 (App Router), TypeScript, Tailwind CSS |
| API サーバー | FastAPI + Uvicorn, Supabase JWT 認証 |
| ML | LightGBM, scikit-learn, Optuna, IsotonicRegression |
| DB | SQLite (`keiba_ultimate.db`), Supabase (ユーザーデータ) |
| スクレイピング | Python requests + BeautifulSoup (netkeiba.com) |

---

## 2. データ取得フロー（スクレイピング）

### 2-1. フロントエンド操作

```
[/data-collection ページ]
  ↓ 期間指定（開始年月〜終了年月）＋ 強制再取得フラグ
  ↓ useBatchScrape フック
    for month in months:
      POST /api/scrape  → { job_id }
      ↓ 3秒ポーリング
      GET /api/scrape/status/{job_id}  → { status, progress }
      ↓ completed になったら次の月へ
```

### 2-2. API → スクレイピング処理

```
POST /api/scrape
  { start_date: "YYYYMMDD", end_date: "YYYYMMDD", force_rescrape: bool }
  ↓
routers/scrape.py
  ↓ バックグラウンドスレッド起動（Windows ProactorEventLoop 対応）
  ↓
  scraping/ モジュール
    └─ netkeiba.com race list → race_id 一覧取得
        └─ 各 race_id: shutuba (出馬表) + result (着順) + payout (払戻)
            └─ SQLite: races_ultimate + race_results_ultimate + return_tables_ultimate
```

### 2-3. DB テーブル構成

| テーブル | 内容 |
|---|---|
| `races_ultimate` | レース基本情報（日付、会場、距離、馬場、天気、ラップ等）|
| `race_results_ultimate` | 出走馬×着順（馬名、騎手、調教師、オッズ、コーナー通過順等）|
| `return_tables_ultimate` | 払戻テーブル（単勝、複勝、三連単等）|

---

## 3. データ読み込み・前処理フロー

**ファイル**: `keiba/keiba_ai/db_ultimate_loader.py`

```
load_ultimate_training_frame(db_path) → pd.DataFrame
  ↓
  1. races_ultimate を読み込み
     - _invalid_distance=True のレースをフィルタ除外
  ↓
  2. race_results_ultimate と JOIN
     - 列名マッピング（Ultimate スキーマ → 標準スキーマ）
       finish_position → finish
       track_type      → surface
       weight_kg       → horse_weight
       jockey_weight   → burden_weight
  ↓
  3. return_tables_ultimate から払戻データを結合
     - tansho（単勝）, fukusho_min/max（複勝）, sanrentan（三連単）
  ↓
  4. ID 抽出（URL から jockey_id, trainer_id, horse_id）
  ↓
  5. コーナー通過順パース "7-7-2-2" → [7, 7, 2, 2]
  ↓
  6. 会場コード解決 ("05" → "東京", "65" → "帯広(ばんえい)")
  ↓
出力: ~200 列の DataFrame（1行 = 1頭の出走エントリ）
```

---

## 4. 特徴量エンジニアリングフロー

**ファイル**: `keiba/keiba_ai/feature_engineering.py`

### 公開 API

```python
add_derived_features(df, full_history_df=None) → pd.DataFrame
```

### パイプライン全体像（10 ステージ順次実行）

```
入力: 生 DataFrame（DB から取得済み）
        ↓
 Stage 1  _fe_days_from_history()   前走日数の計算（時系列ソート）
        ↓
 Stage 2  _fe_horse_category()      馬カテゴリ・年齢・コーナー指標
        ↓
 Stage 3  _fe_id_season()           レースID解析・季節性・レースクラス数値化
        ↓
 Stage 4  _fe_course()              コース特性（直線長・内外バイアス）
        ↓
 Stage 5  _fe_market()              オッズ派生特徴量・市場エントロピー
        ↓
 Stage 6  _fe_prev_race()           前走スピード指標・休養日数
        ↓
 Stage 7  _fe_missing_flags()       欠損フラグ生成
        ↓
 Stage 8  _fe_lap()                 ラップペース特徴量（前半/後半分割）
        ↓
 Stage 9  _fe_payout()              配当派生特徴量（log変換・z-スコア）
        ↓
 Stage 10 _fe_history()             履歴ベース展開統計（10 サブ関数）
        ↓
出力: 90+ 特徴量列が追加された DataFrame
```

### Stage 10: `_fe_history()` サブ関数詳細

各サブ関数は `(df, h) → (df, h)` のシグネチャで、`h=full_history_df` を展開ウィンドウの母集合として使用。
**重要**: 全てデータリーク防止のため「過去レースのみ」を使う expanding window 集計。

| サブ関数 | 生成特徴量 | 集計方法 |
|---|---|---|
| `_feh_jockey_course` | `jockey_course_win_rate`, `jockey_course_races` | 騎手×会場別 expanding 勝率 |
| `_feh_horse_aptitude` | `horse_distance_*`, `horse_surface_*`, `horse_dist_band_*`, `horse_venue_*`, `horse_venue_surface_*`, `horse_dist_surface_*` | 馬×距離/馬場/会場/複合 expanding 6グループ |
| `_feh_gate_bias` | `gate_win_rate` | 会場×距離帯×馬場×内外枠 静的集計（最低10回） |
| `_feh_jt_combo` | `jt_combo_races`, `jt_combo_win_rate`, `jt_combo_win_rate_smooth` | 騎手×調教師 expanding + ベイズ平滑化 (K=5, prior=0.075) |
| `_feh_entity_career` | `jockey_win_rate/show_rate/place_rate_top2`, `trainer_*`, `sire_*`, `damsire_*` | 騎手・調教師・父・母父 expanding 通算成績 |
| `_feh_recent_form` | `past3/5/10_avg_finish`, `past3/5_win_rate` | 馬の近走 rolling (3/5/10 走) |
| `_feh_entity_recent30` | `jockey_recent30_win_rate`, `trainer_recent30_win_rate` | 騎手・調教師 近30走 rolling |
| `_feh_last_3f` | `past3/5_avg_last3f_time`, `past3_avg_last3f_rank` | 上がり3F rolling (3/5 走) |
| `_feh_payout_history` | `past5_avg_tansho_log` | 過去単勝配当 rolling (5 走) |
| `_feh_running_style` | `running_style_mean_5`, `running_style_std_5` | 脚質数値 rolling (5 走) |

### ベイズ平滑化（ジョッキー×調教師コンボ）

$$\text{smooth\_wr} = \frac{n \times \text{raw\_wr} + K \times \text{global\_wr}}{n + K}$$

- $K = 5$（スムージング強度）
- $\text{global\_wr} = 0.075$（全体勝率の事前分布）

### 特徴量カテゴリ一覧（90+ 特徴量）

```
エントリー基本:  horse_no, bracket, age, handicap, weight, weight_diff,
                 entry_odds, entry_popularity, n_horses

コース特性:      straight_length, inner_bias, inner_advantage,
                 track_type, corner_radius, venue_code

季節性:          cos_date, sin_date, seasonal_sex, frame_race_type

馬のカテゴリ:    is_young, is_prime, is_veteran, corner_mean, corner_var

騎手・調教師:    jockey_course_win_rate, jockey_place_rate_top2,
                 jockey_show_rate, jockey_recent30_win_rate,
                 fe_trainer_win_rate, trainer_place_rate_top2,
                 trainer_show_rate, trainer_recent30_win_rate,
                 jt_combo_win_rate_smooth, jt_combo_races

血統:            sire_win_rate, sire_show_rate,
                 damsire_win_rate, damsire_show_rate

馬の条件別適性:  horse_distance_win_rate, horse_distance_avg_finish,
                 horse_surface_win_rate, horse_dist_band_win_rate,
                 horse_venue_win_rate, horse_venue_surface_win_rate,
                 horse_dist_surface_win_rate  （+ _races カウント）

近走成績:        past3/5/10_avg_finish, past3/5_win_rate, horse_win_rate

スピード指標:    prev_speed_index, prev_speed_zscore,
                 prev_race_time_seconds, distance_change

前走日数:        days_since_last_race

オッズ派生:      implied_prob_norm, odds_rank_in_race, odds_z_in_race,
                 market_entropy, top3_probability

馬場バイアス:    gate_win_rate

ラップペース:    race_pace_front, race_pace_back,
                 race_pace_diff, race_pace_ratio

配当派生:        tansho_payout_log, sanrentan_payout_log,
                 sanrentan_z_in_races

上がり3F:        past3/5_avg_last3f_time, past3_avg_last3f_rank,
                 past5_avg_tansho_log

脚質:            running_style (カテゴリ), running_style_num,
                 running_style_mean_5, running_style_std_5

レースクラス:    race_class_num (G1=8 〜 新馬=-1)

欠損フラグ:      prev_race_finish_is_missing, days_since_last_race_is_missing,
                 prev_speed_index_is_missing, horse_win_rate_is_missing,
                 odds_is_missing
```

---

## 5. モデル学習フロー

**ファイル**: `keiba/keiba_ai/train.py`

### 学習パイプライン全体

```
train(cfg_path: Path) → Path (モデルファイルパス)
  ↓
  1. 設定読み込み (config.yaml)
     - target: "win" | "place3" | "win_tie"
     - model_type: "lightgbm" | "logistic"
     - lgbm_num_boost_round, test_size, etc.
  ↓
  2. データ読み込み
     load_ultimate_training_frame(db_path) → raw DataFrame
  ↓
  3. 特徴量エンジニアリング
     add_derived_features(df, full_history_df=df)  → +90 特徴量
  ↓
  4. 特徴量列の動的構築
     _build_feature_columns(df) → (feature_cols_num, feature_cols_cat)
     ※ _NUM_FEATURE_CANDIDATES / _CAT_FEATURE_CANDIDATES の候補リストから
       DataFrame に実在する列のみを自動選択
  ↓
  5. ターゲット生成
     _make_target(df, target) → pd.Series (binary 0/1)
     - "win":      finish == 1
     - "place3":   finish <= 3
     - "win_tie":  同着考慮の1着
  ↓
  6. 学習/テスト分割
     - race_date が存在: 日付ベースカットオフ（時系列順守）
     - それ以外: random 80/20
  ↓
  7. モデル学習
     _train_lightgbm() or _train_logistic()
  ↓
  8. 確率キャリブレーション
     IsotonicRegression.fit(p_train, y_train) → calibrator
  ↓
  9. モデル保存
     joblib.dump(bundle, models/model_win_YYYYMMDD_HHMMSS.joblib)
  ↓
出力: モデルファイルパス
```

### `_build_feature_columns(df)` 詳細

```python
# 候補リストから df に存在する列のみを返す
num_cols = [c for c in _NUM_FEATURE_CANDIDATES if c in df.columns]
cat_cols = [c for c in _CAT_FEATURE_CANDIDATES if c in df.columns]
return num_cols, cat_cols
```

新しい特徴量を追加したい場合は `_NUM_FEATURE_CANDIDATES` / `_CAT_FEATURE_CANDIDATES` に追記するだけでよい。

### `_train_lightgbm()` 詳細

```
入力: X_train, y_train, X_test, y_test, feature_cols_num, feature_cols_cat,
      ev_weighted=False, entry_odds_train=None

前処理 ColumnTransformer:
  - Numeric  → SimpleImputer(strategy="median")
  - Categor. → OneHotEncoder(handle_unknown="ignore")

LightGBM ハイパーパラメータ（デフォルト）:
  n_estimators     : config.lgbm_num_boost_round (e.g. 500)
  learning_rate    : 0.05
  num_leaves       : 63
  min_child_samples: 20
  colsample_bytree : 0.8
  subsample        : 0.8
  reg_alpha        : 0.1
  reg_lambda       : 0.1

EV ウェイト学習（ev_weighted=True の場合）:
  _compute_ev_weights():
    1. OOF 5-fold で LightGBM (200 round) を学習
    2. OOF 予測確率から EV = p × odds - 1 を計算
    3. weight = sigmoid((EV - 0) / 0.5) + 1
    4. 全体の mean=1.0 に正規化
  ↓ sample_weight として LightGBM に渡す

出力: Pipeline(preprocessor + LGBMClassifier)
      ROC-AUC, logloss (test set)
      feature_importance DataFrame
```

### 保存モデル (joblib バンドル) の構造

```python
{
    "model":            Pipeline,           # preprocessor + classifier
    "feature_cols_num": List[str],          # 数値特徴量列名
    "feature_cols_cat": List[str],          # カテゴリ特徴量列名
    "target":           str,                # "win" | "place3" | "win_tie"
    "model_type":       str,                # "lightgbm" | "logistic"
    "metrics": {
        "auc":          float,
        "logloss":      float,
    },
    "created_at":       str,                # "YYYYMMDD_HHMMSS"
    "feature_importance": pd.DataFrame,    # feature, importance
    "calibrator":       IsotonicRegression,
}
```

---

## 6. 予測フロー

### 6-1. CLI 予測（`predict.py`）

```
predict_race(cfg_path, model_path, race_id, topk=5) → pd.DataFrame
  ↓
  1. joblib.load(model_path) → bundle
  ↓
  2. netkeiba.com から shutuba (出馬表) スクレイプ
  ↓
  3. add_derived_features(df) で特徴量生成
  ↓
  4. 特徴量列を揃える（bundle の feature_cols に従う）
  ↓
  5. bundle["model"].predict_proba(X)[:, 1] → p_win_like
  ↓
  6. bundle["calibrator"].predict(p_win_like) → calibrated_score
  ↓
  7. predicted_rank 降順ソート → top-k 返却
  ↓
出力: bracket, horse_no, horse_name, jockey_name, odds, p_win_like
```

### 6-2. API 予測（`/api/analyze-race`）

```
POST /api/analyze-race
  { race_id, bankroll, risk_mode }
  ↓
routers/predict.py
  ↓
  1. DB から race_id の出走馬データ取得
  ↓
  2. add_derived_features() で特徴量生成
  ↓
  3. latest model (or 指定 model_id) で predict_proba
  ↓
  4. 確率正規化（全頭の確率の合計→1.0）
  ↓
  5. expected_value = p_norm × odds 計算
  ↓
  6. BettingStrategy で推奨馬券・購入点数を計算
     - Kelly criterion でベット額算出
     - best_bet_type: 単勝 / 馬連 / 三連単 から EV 最大を選択
  ↓
  7. 5分 TTL でレスポンスをキャッシュ
  ↓
出力: {
  race_info, predictions (predicted_rank, p_raw, p_norm, EV),
  recommendation (action, purchase_count, unit_price, expected_return),
  best_bet_type, pro_evaluation (race_level, confidence),
  bet_types (組み合わせ一覧)
}
```

---

## 7. API エンドポイント一覧

### スクレイピング関連

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/scrape` | 期間指定スクレイプ起動 → `{ job_id }` |
| POST | `/api/scrape/start` | 同上（別バリアント） |
| GET  | `/api/scrape/status/{job_id}` | ジョブ進捗ポーリング |
| POST | `/api/scrape/repair/{race_id}` | 特定レースの再取得 |
| POST | `/api/rescrape_incomplete` | 未完了データの一括再取得 |

### 学習関連

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/train` | 同期学習（レガシー） |
| POST | `/api/train/start` | 非同期学習ジョブ起動 |
| GET  | `/api/train/status/{job_id}` | 学習ジョブ進捗ポーリング |

### 予測関連

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/predict` | 汎用予測（horses 配列受付） |
| POST | `/api/analyze-race` | レース分析（推奨含む） |
| POST | `/api/analyze_races_batch` | 複数レース一括分析 |

### モデル管理

| メソッド | パス | 説明 |
|---|---|---|
| GET    | `/api/models` | モデル一覧 |
| GET    | `/api/models/{model_id}` | 特定モデル詳細 |
| DELETE | `/api/models/{model_id}` | モデル削除 |

### レース・データ

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/races/recent` | 最近取得したレース一覧 |
| GET | `/api/races/by-date?date=YYYYMMDD` | 日付指定レース一覧 |
| GET | `/api/data-stats` | DB統計（総レース数等） |
| GET | `/api/debug/race/{race_id}` | レース詳細デバッグ情報 |
| GET | `/api/debug/race/{race_id}/features` | 特徴量デバッグ情報 |

### デバッグ・管理

| メソッド | パス | 説明 |
|---|---|---|
| GET  | `/` | ヘルスチェック |
| GET  | `/api/debug` | Supabase接続確認 |
| GET  | `/api/export-data` | CSV エクスポート |
| GET  | `/api/export-db` | SQLite DB ダウンロード |
| DELETE | `/api/data/all` | 全データ削除（要確認） |
| POST | `/api/profiling/start` | 特徴量プロファイリング起動 |
| GET  | `/api/profiling/status/{job_id}` | プロファイリング進捗 |
| GET  | `/api/profiling/html/{job_id}` | HTML レポート取得 |
| POST | `/api/backfill/nar-pedigree` | NAR 血統データ補完 |

### 購入履歴

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/api/purchase` | 購入記録登録 |
| GET  | `/api/purchase_history` | 購入履歴取得 |
| GET  | `/api/statistics` | 集計統計（bet_type別・月別ROI） |

---

## 8. フロントエンド構成

### ページ構成（App Router）

```
src/app/
├─ page.tsx               ランディングページ
├─ home/page.tsx          ダッシュボードハブ（システム状態・ナビ）
├─ data-collection/       スクレイピング UI
│   └─ page.tsx
├─ train/page.tsx         モデル学習 UI
├─ predict-batch/page.tsx バッチ予測 UI
├─ race-analysis/         レース分析 UI
│   └─ page.tsx           ← useRaceCache + RacePredictionPanel + RaceFeaturePanel
├─ dashboard/page.tsx     購入履歴・ROI 分析
├─ admin/page.tsx         管理者機能（権限制御）
└─ data-view/page.tsx     生データブラウザ
```

### カスタムフック

| フック | 役割 |
|---|---|
| `useJobPoller` | 汎用ジョブポーリング（3秒間隔、最大10分、完了/エラーで自動停止） |
| `useBatchScrape` | 月単位ループ＋ポーリングのバッチスクレイプ処理をカプセル化 |
| `useRaceCache` | インメモリ Map + localStorage (TTL 5分) の2層キャッシュ |
| `useScrape` | 単一月スクレイプジョブ起動 |
| `useUserRole` | Supabase JWT からユーザーロール（isAdmin/isLogged）取得 |

### コンポーネント

| コンポーネント | 役割 |
|---|---|
| `RacePredictionPanel` | 予測タブ（推奨カード＋買い目＋馬一覧＋確率バー） |
| `RaceFeaturePanel` | 特徴量分析タブ（グループ別表示チップ＋z-スコア色付き表） |
| `useRaceCache.ts` | レース予測データキャッシュ管理 |
| `Toast` | 成功/エラートースト通知 |
| `ConfirmDialog` | 破壊的操作の確認ダイアログ |
| `AdminOnly` | 管理者のみ表示するラッパー |
| `Logo` | クリックでホームへ遷移するブランドロゴ |
| `ErrorBoundary` | React エラー境界（フォールバック UI） |

### データフロー（race-analysis ページの例）

```
[race-analysis/page.tsx]
  ↓ 日付選択
  GET /api/races/by-date?date=YYYYMMDD → レース一覧表示
  ↓ レース選択
  useRaceCache.get(raceId)
    → キャッシュヒット: 即時表示（キャッシュ済みバッジ表示）
    → キャッシュミス:
        Promise.all([
          POST /api/analyze-race,
          GET  /api/debug/race/{raceId}/features
        ])
        → useRaceCache.set(raceId, entry)
        → RacePredictionPanel (予測タブ)
        → RaceFeaturePanel   (特徴量タブ)
```

---

## 9. データモデル（DB スキーマ概要）

### keiba_ultimate.db（SQLite）

```sql
races_ultimate
  race_id       TEXT PRIMARY KEY    -- "YYYYMMDDVVRR"
  race_date     TEXT                -- "YYYY-MM-DD"
  venue_code    TEXT                -- "05" = 東京
  distance      INTEGER             -- メートル
  track_type    TEXT                -- "芝" | "ダート" | "障害"
  weather       TEXT
  field_condition TEXT
  race_name     TEXT
  race_class    TEXT
  num_horses    INTEGER
  lap_cumulative  TEXT (JSON)       -- [34.2, 69.5, ...] 累積タイム
  lap_sectional   TEXT (JSON)       -- [11.4, 12.1, ...] 区間タイム
  _invalid_distance BOOLEAN         -- スクレイプ失敗フラグ

race_results_ultimate
  id              INTEGER PK
  race_id         TEXT FK → races_ultimate
  horse_id        TEXT
  horse_name      TEXT
  finish_position INTEGER           -- 着順 (NULL = 競走中止等)
  bracket_number  INTEGER           -- 枠番
  horse_number    INTEGER           -- 馬番
  age             INTEGER
  sex             TEXT
  jockey_id       TEXT
  jockey_name     TEXT
  trainer_id      TEXT
  trainer_name    TEXT
  sire_id         TEXT
  damsire_id      TEXT
  entry_odds      REAL              -- 単勝オッズ
  popularity      INTEGER           -- 人気順位
  horse_weight    REAL              -- 馬体重 (kg)
  weight_change   REAL              -- 増減
  finish_time     TEXT              -- "1:34.2"
  last_3f         REAL              -- 上がり3F (秒)
  corners         TEXT (JSON)       -- "[7,7,2,2]"
  running_style   TEXT              -- 脚質（FE後に付加）

return_tables_ultimate
  race_id         TEXT FK
  tansho          REAL              -- 単勝払戻
  fukusho_min     REAL              -- 複勝最低
  fukusho_max     REAL              -- 複勝最高
  sanrentan       REAL              -- 三連単払戻
```

### モデルファイル（joblib）

```
keiba/models/
└─ model_win_YYYYMMDD_HHMMSS.joblib
   (内容: 上記「保存モデルの構造」参照)
```

### Supabase（ユーザーデータ）

- `users` テーブル: ユーザーID、メール、ロール（admin/user）
- `purchase_history` テーブル: 購入記録（race_id, bet_type, combinations, cost, return, recovery_rate）
- JWT ベース認証（FastAPI の Supabase ミドルウェアで検証）

---

## 付録: 主要ファイルマップ

```
keiba-ai-pro/
├─ keiba/keiba_ai/
│   ├─ feature_engineering.py  ← 特徴量パイプライン（Stage 1-10）
│   ├─ train.py                ← 学習パイプライン + _build_feature_columns
│   ├─ predict.py              ← CLI 予測
│   ├─ db_ultimate_loader.py   ← DB 読み込み・前処理
│   ├─ config.yaml             ← 学習設定
│   └─ tests/                  ← pytest (73 passed, 6 skipped)
├─ python-api/
│   ├─ main.py                 ← FastAPI アプリ・ミドルウェア設定
│   ├─ routers/                ← エンドポイント実装
│   │   ├─ predict.py, train.py, scrape.py, ...
│   ├─ betting_strategy.py     ← Kelly 基準・買い目推奨
│   └─ models.py               ← Pydantic スキーマ
├─ src/
│   ├─ app/                    ← Next.js ページ
│   ├─ hooks/                  ← カスタムフック
│   ├─ components/             ← UI コンポーネント
│   └─ lib/
│       ├─ types.ts            ← 共有型定義
│       └─ race-analysis-types.ts ← レース分析専用型
└─ supabase/
    └─ schema.sql              ← DB スキーマ定義
```
