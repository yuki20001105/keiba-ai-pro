# 自動モデル学習システム 設計仕様書

## 概要

競馬レースは毎週開催されるため、最新の結果データを自動的に学習し続けることで予測精度を維持・向上させる仕組みを構築する。

---

## 要件定義

| # | 要件 | 詳細 |
|---|---|---|
| ① | **重複学習防止** | 同じデータを二度学習しない。学習済みデータを管理するフラグ／ログが必要 |
| ② | **データリーク防止** | 結果が確定していないレース（予測対象）を学習データに混入させない |
| ③ | **24時間稼働** | FastAPI を落とさずバックグラウンドで自動学習を行う |

---

## 設計方針

### ① 重複学習防止：`model_training_log` テーブル

学習済みの「最大レース日付（cutoff_date）」をDBに記録し、それ以降の新データが存在する場合のみ再学習する。

```sql
-- schema_ultimate.sql に追加
CREATE TABLE IF NOT EXISTS model_training_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id      TEXT    NOT NULL,               -- モデルファイル名（タイムスタンプ付き）
  model_path    TEXT    NOT NULL,               -- 絶対パス
  trained_at    TEXT    NOT NULL,               -- 学習実行日時 (JST, ISO8601)
  cutoff_date   TEXT    NOT NULL,               -- このモデルに含まれた最大レース日付 (YYYYMMDD)
  race_count    INTEGER,                        -- 学習に使用したレース数
  sample_count  INTEGER,                        -- 学習に使用したサンプル数（行数）
  target        TEXT,                           -- "win" or "place3"
  auc           REAL,                           -- 検証AUC
  logloss       REAL,                           -- 検証LogLoss
  trigger       TEXT DEFAULT 'manual',          -- "auto" or "manual"
  notes         TEXT                            -- 備考
);
```

**再学習の判定ロジック：**

```
1. model_training_log から最新の cutoff_date を取得
2. DB: SELECT MAX(kaisai_date) FROM race_results_ultimate → latest_result_date
3. latest_result_date > cutoff_date であれば学習を起動
4. そうでなければスキップ（「新しいデータなし」とログ記録）
```

---

### ② データリーク防止：学習対象フィルタの徹底

| テーブル | 用途 | 条件 |
|---|---|---|
| `race_results_ultimate` | 学習データ | `finish IS NOT NULL`（結果確定済みのみ） |
| `entries` | 予測用入力データ | 出走登録時点（結果なし） |

`load_ultimate_training_frame()` に明示的フィルタを追加：

```python
# db_ultimate_loader.py
def load_ultimate_training_frame(db_path, cutoff_date=None):
    """
    cutoff_date: この日付以前のレース結果のみを返す（未来リーク防止）
    finish IS NOT NULL: 結果未確定レースを除外（データリーク防止）
    """
    query = """
        SELECT * FROM race_results_ultimate
        WHERE finish IS NOT NULL        -- 結果未確定を除外
    """
    if cutoff_date:
        query += f" AND kaisai_date <= '{cutoff_date}'"
    ...
```

**自動学習時の cutoff_date の決め方：**
- レース結果が確定するのは当日17〜19時以降
- 安全のため **「当日の2日前まで」** を cutoff_date にする
- `cutoff_date = today - timedelta(days=2)`

---

### ③ 24時間稼働 + バックグラウンド学習

FastAPI の非同期スケジューラ + ProcessPoolExecutor で実現する。

**なぜ ProcessPoolExecutor が必要か：**

```
FastAPI（asyncio イベントループ）
  └── 学習処理（CPU負荷大）を直接実行
        → イベントループがブロック
        → API が応答しなくなる  ← NG

FastAPI（asyncio イベントループ）
  └── await loop.run_in_executor(ProcessPoolExecutor, 学習関数)
        → 別プロセスで学習（APIは継続稼働）  ← OK
```

**スケジューラの実装方針：**

```python
# main.py に追加

from concurrent.futures import ProcessPoolExecutor
import asyncio

executor = ProcessPoolExecutor(max_workers=1)  # 同時学習は1プロセスのみ
_training_lock = asyncio.Lock()               # 二重起動防止

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_train_scheduler())

async def auto_train_scheduler():
    """毎時チェック → 新データあれば自動学習"""
    await asyncio.sleep(60)  # 起動直後は60秒待機
    while True:
        try:
            await maybe_run_auto_train()
        except Exception as e:
            logger.error(f"[AutoTrain] スケジューラエラー: {e}")
        await asyncio.sleep(3600)  # 1時間ごとにチェック

async def maybe_run_auto_train():
    """新データが存在すれば学習を起動"""
    if _training_lock.locked():
        logger.info("[AutoTrain] 学習中のためスキップ")
        return

    if not should_retrain():     # 新データ判定
        logger.info("[AutoTrain] 新データなし、スキップ")
        return

    async with _training_lock:
        loop = asyncio.get_event_loop()
        logger.info("[AutoTrain] 学習開始（バックグラウンド）")
        await loop.run_in_executor(executor, run_training_process)
        logger.info("[AutoTrain] 学習完了")
```

---

## 全体フロー

```
[毎時 スケジューラ起動]
         │
         ▼
  _training_lock をチェック
         │
    ロック中? ──YES──→ スキップ
         │NO
         ▼
  model_training_log の最新 cutoff_date を取得
         │
         ▼
  DB: MAX(kaisai_date) from race_results_ultimate
         │
  新データあり? ──NO──→ "新データなし" をログ記録 → 終了
         │YES
         ▼
  ProcessPoolExecutor で学習プロセス起動
  （FastAPI は継続して API リクエストを処理）
         │
         ▼
  学習完了
         │
         ├─ model_training_log に記録（cutoff_date, AUC, etc.）
         ├─ models/ に新モデルファイルを保存
         └─ 古いモデルを削除（最新 N 本のみ保持）
```

---

## APIエンドポイント（新規追加予定）

| エンドポイント | メソッド | 説明 |
|---|---|---|
| `/api/auto-train/status` | GET | スケジューラの状態、学習中かどうか、最終学習日時 |
| `/api/auto-train/history` | GET | `model_training_log` の一覧（AUC推移） |
| `/api/auto-train/trigger` | POST | 手動で即時学習を起動 |
| `/api/auto-train/config` | GET/PUT | チェック間隔・保持モデル数などの設定変更 |

---

## 設定パラメータ（config.yaml に追加予定）

```yaml
auto_train:
  enabled: true               # 自動学習のON/OFF
  check_interval_hours: 6     # チェック間隔（時間）
  min_new_races: 10           # 再学習に必要な最小新規レース数
  cutoff_lag_days: 2          # 今日から何日前までを学習対象にするか（リーク防止）
  keep_models: 5              # 保持するモデルファイルの最大数
  train_time_limit_min: 30    # 学習タイムアウト（分）
```

---

## 実装対象ファイル

| ファイル | 変更種別 | 変更内容 |
|---|---|---|
| `keiba/keiba_ai/schema_ultimate.sql` | 追加 | `model_training_log` テーブル定義 |
| `keiba/keiba_ai/db_ultimate.py` | 追加 | `get_latest_cutoff_date()`, `insert_training_log()` |
| `keiba/keiba_ai/db_ultimate_loader.py` | 修正 | `cutoff_date` パラメータ追加、`finish IS NOT NULL` フィルタ明示化 |
| `keiba/config.yaml` | 追加 | `auto_train:` セクション追加 |
| `keiba/keiba_ai/config.py` | 追加 | `AutoTrainConfig` dataclass |
| `python-api/main.py` | 追加 | スケジューラ・バックグラウンド学習・ステータスAPI |

---

## 注意事項・リスク

| リスク | 対策 |
|---|---|
| 学習中にAPIがメモリ不足になる | `ProcessPoolExecutor(max_workers=1)` で同時実行1プロセスに制限 |
| 学習が終わらずデッドロック | `train_time_limit_min` でタイムアウト設定 |
| 学習失敗で古いモデルが消える | 学習成功を確認してから古いモデルを削除 |
| 競馬開催のない週（夏季・冬季休み） | `min_new_races` 閾値でスキップ |
| SQLite へのWrite競合 | WALモード（既存設定）で読み書き競合を軽減 |

---

## 実装順序（推奨）

1. **`schema_ultimate.sql`** に `model_training_log` を追加（DBマイグレーション）
2. **`db_ultimate.py`** にログ読み書き関数を追加
3. **`db_ultimate_loader.py`** のフィルタ修正（データリーク防止を確実に）
4. **`config.yaml` / `config.py`** に `auto_train` 設定を追加
5. **`main.py`** にスケジューラとバックグラウンド学習を追加
6. **動作確認**：手動トリガー → ログ確認 → 24時間稼働テスト

---

*作成日: 2026-02-21*
*前提: patch_missing_data.py によるデータ補完完了後に実装開始*
