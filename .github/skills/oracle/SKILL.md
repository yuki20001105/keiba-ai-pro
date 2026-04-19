---
name: oracle
description: 'Oracle（オラクル）スキル — 予測実行・Kelly計算・買い目生成担当。Use when: レースの予測を実行したい / analyze_race エラーが出る / Kelly基準による買い目推奨を調整したい / オッズ取得に失敗する / 予測結果の表示がおかしい / race-analysis / predict-batch ページの改修 / 結果照合・prediction_log の調査 / バッチ予測の速度・安定性を改善したい。Keywords: 予測, predict, analyze_race, Kelly, オッズ, race-analysis, predict-batch, prediction_log, 買い目, 勝率, win_probability, 単勝, 複勝, kelly_fraction, 推奨馬券, INV-02, INV-04'
---

# Oracle（オラクル）— 予測実行・Kelly計算・買い目生成

学習済みモデルを使ってレースの勝率を予測し、Kelly基準で馬券購入推奨を出力する。

---

## 担当ページ・ファイル

| 種別 | パス | 役割 |
|---|---|---|
| UI | `src/app/race-analysis/page.tsx` | 単一レース詳細予測・結果照合 |
| UI | `src/app/predict-batch/page.tsx` | 複数レース一括予測・買い目生成 |
| API | `python-api/routers/predict.py` | 予測エンドポイント本体 |
| API | `python-api/routers/prediction_history.py` | 予測履歴・結果照合 |
| API | `python-api/routers/realtime_odds.py` | リアルタイムオッズ取得 |
| Core | `python-api/betting/` | Kelly計算・馬券推奨ロジック |
| Hook | `src/hooks/useRaceCache.ts` | 予測キャッシュ管理（L1メモリ + L2 localStorage） |

---

## 予測パイプライン（INV-01 厳守）

```
POST /api/analyze-race  { race_id, model_id?, bankroll, risk_mode }
    ↓
SQLite から entries テーブル読み込み
    ↓
add_derived_features()  ← 特徴量生成
    ↓
POST_RACE_FIELDS 除外   ← ★省略禁止
    ↓
LightGBM.predict_proba()  → win_probability（勝率）
    ↓
Kelly計算  → kelly_fraction・推奨馬券種・buy_amount
    ↓
prediction_log テーブルに保存
    ↓
レスポンス返却
```

---

## 主要 API エンドポイント

| エンドポイント | 説明 |
|---|---|
| `POST /api/analyze-race` | 単一レース予測（メイン） |
| `GET /api/races/by-date?date=YYYYMMDD` | 日付別レース一覧 |
| `GET /api/models?ultimate=true` | モデル一覧（no_odds除外・降順） |
| `GET /api/prediction-history/{race_id}` | 特定レースの予測 vs 実績 |
| `GET /api/prediction-history` | 全予測履歴（Premium必須） |

---

## 不変条件（必ず守ること）

### INV-02: オッズ判定

```python
# ❌ 禁止（0.0 が falsy で誤判定される）
odds = _hr.get("odds") or _hr.get("win_odds")

# ✅ 正しい
odds = _hr.get("odds")
if odds is None:
    odds = _hr.get("win_odds")

# df_pred 逆引きマップを優先して使うこと
# 全馬 odds=0 or NaN の場合のみ再スクレイプ
```

### INV-04: CONCURRENCY = 1

```typescript
// src/app/predict-batch/page.tsx
const CONCURRENCY = 1  // ← 変更禁止（GIL競合でタイムアウトが発生する）

// predict-batch は必ず逐次処理（並列化しない）
for (const raceId of selectedRaces) {
  await predictSingle(raceId)   // 1レースずつ
}
```

### INV-05: タイムアウト値

```typescript
// UI → Next.js API: 180,000ms 以上
signal: AbortSignal.timeout(180_000)

// Next.js → FastAPI: 300,000ms 以上
export const maxDuration = 300  // 削除禁止
```

---

## prediction_log テーブル

```sql
CREATE TABLE prediction_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id         TEXT,
    race_name       TEXT,
    venue           TEXT,
    race_date       TEXT,
    horse_id        TEXT,
    horse_name      TEXT,
    horse_number    INTEGER,
    predicted_rank  INTEGER,
    win_probability REAL,
    p_raw           REAL,
    odds            REAL,
    popularity      INTEGER,
    model_id        TEXT,
    predicted_at    TEXT
);
-- ユニーク管理: race_id + model_id の組み合わせ（同じ組み合わせは上書き）
```

### 重複除去（結果照合時）

```sql
-- 同一 horse_id の最新予測のみ取得
INNER JOIN (
    SELECT horse_id, MAX(predicted_at) AS latest_at
    FROM prediction_log WHERE race_id = ?
    GROUP BY horse_id
) latest ON pl.horse_id = latest.horse_id
         AND pl.predicted_at = latest.latest_at
```

---

## キャッシュ戦略（useRaceCache）

| 条件 | TTL |
|---|---|
| 過去レース（race_date < today） | 永続（削除しない） |
| 当日・未来レース | 30分（オッズ変動対応） |
| モデル変更時 | 別キー `raceId__modelId` で強制再取得 |

---

## よくあるエラーと対処

### predict-scrape-feature-adaptation スキルを参照すべきケース
- `NameError: name 'asyncio' is not defined`
- `optimizer.transform` 失敗
- IPブロック以外の 500 エラー

### 結果照合で重複馬が表示される
```
原因: prediction_log に同一レースの複数モデル分が保存されている
対処: MAX(predicted_at) INNER JOIN で最新予測のみ取得
（python-api/routers/prediction_history.py 修正済み）
```

### オッズが全馬 0 または NaN
```
原因: スクレイプタイミング（発走前・結果ページ移行後）
対処: INV-02に従い is not None 判定 + 全馬0のときのみ再スクレイプ
```

---

## 関連スキル

| タスク | 参照スキル |
|---|---|
| スクレイプ起因の予測エラー | `predict-scrape-feature-adaptation` |
| 特徴量変更後の推論パイプライン修正 | `predict-scrape-feature-adaptation` |
