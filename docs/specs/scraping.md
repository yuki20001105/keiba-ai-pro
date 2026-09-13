# スクレイピング仕様書（Scraping Spec）

> **対象ファイル**: `python-api/scraping/jobs.py`, `race.py`, `horse.py`, `storage.py`, `constants.py`  
> **参照**: [SYSTEM.md](./SYSTEM.md) — INV-07（インターバル）、INV-08（認証）

---

## 1. Requirement（満たすべき条件）

| ID | 条件 |
|---|---|
| R-SC-01 | システムは netkeiba.com からレース・馬データを自動取得する |
| R-SC-02 | システムは取得済み日付をスキップし、再実行時に再開できる |
| R-SC-03 | システムは各ページリクエスト間に最低 1 秒のインターバルを設ける |
| R-SC-04 | システムは HTTP 429 時に全 netkeiba 通信を共有休止し、Retry-After より早く再開しない |
| R-SC-05 | システムは取得データを `keiba_ultimate.db` に保存する |
| R-SC-06 | システムは進捗（done/total）をポーリングで返す |
| R-SC-07 | システムは過去 30 日超のデータはカレンダーで開催日を絞り込む |

---

## 2. Specification（仕様）

### 2-1. エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `POST` | `/api/scrape/start` | スクレイプジョブを開始し `job_id` を即時返却 |
| `POST` | `/api/scrape/cancel/{job_id}` | 所有者Adminが停止要求を永続化し、安全な処理境界で個別jobを停止 |
| `GET` | `/api/scrape/status/{job_id}` | 進捗を返す（ポーリング用） |
| `GET` | `/api/scrape/jobs` | 全ジョブ一覧 |
| `DELETE` | `/api/scrape/jobs/{job_id}` | ジョブ削除 |

### 2-2. リクエスト

```json
POST /api/scrape/start
{
  "start_date": "20260101",   // YYYYMMDD
  "end_date":   "20260413",   // YYYYMMDD
  "force_rescrape": false     // true = 日付を再検査し不足・品質未達レースを修復（品質合格レースはスキップ）
}
```

### 2-3. レスポンス（status）

```json
{
  "job_id": "uuid-string",
  "status": "queued | running | cancelling | cancelled | completed | error",
  "progress": {
    "done": 5,
    "total": 12,
    "message": "5/12日処理済み / 40レース保存",
    "saved_races": 40,
    "saved_horses": 480
  }
}
```

---

## 3. 処理フロー（Process Flow）

```
POST /api/scrape/start
  ↓ job_id 即時返却（バックグラウンド実行）
  ↓
[前処理A] 過去 30 日超 → カレンダーから開催日を取得して日付を絞り込む
[前処理B] SQLite の完了・品質台帳で再開対象を確定
  ↓
[日付ループ] dates[] を 1 件ずつ処理
  ├─ 品質合格済み → スキップ
  ├─ 日付一覧 / 検証済み月別索引 → race_id[] 取得
  │    └─ 空応答・400 だけでは開催なしと確定しない
  ├─ [レースループ] 不足・品質未達レースのみ処理
  │    ├─ 30 日超の過去: 有効な保存 HTML を再解析、品質不足なら再通信
  │    └─ 直近: 従来どおりレース本体を新鮮な応答で取得
  ├─ 馬情報・血統を補完（出典・期限・パーサー版が一致する解析結果を再利用）
  ├─ 品質検査 → _save_race_sqlite_only() → 品質台帳・完了記録
  └─ 停止要求があれば、一貫した保存境界で終了

各実通信は共通制御器を通す（カレンダー・オッズ・ブラウザ経路を含む）。
429 → 共有待機 / 401・403・制限 HTML → 自動通信停止。
```

---

## 4. インターバル仕様（Interval Spec）

> **INV-07 より**: netkeiba 全体のリクエスト開始間隔は最低 **1.0 秒**、同時通信は **1**。

`request_pacing.py` の SQLite lease によってプロセス間でも発行枠を共有する。
初期 2 秒。ページ種別ごとの正常応答が 50 件以上かつ 120 秒以上安定すると 10% ずつ短縮する。
応答遅延・5xx・タイムアウトでは減速する。キャッシュ処理に日付・レース単位の固定待機を追加しない。
制御 DB の故障時に無制御の通信へフォールバックしない。

---

## 5. リトライ仕様（Retry Spec）

`fetch_pipeline.py` が再試行を管理し、各試行・リダイレクトも共有発行枠を取得する。

- 429: `Retry-After`（秒数・HTTP 日時）と内部 5 分以上の指数待機の長い方を共有。再起動でも期限を維持。
- 401・403・制限 HTML: `FetchAccessBlocked` で停止。他サブドメインへの切替・自動再試行は行わない。
- 400: 開催なしや互換性問題と即断しない。別形式で妥当な結果を取得できた場合のみ、ページ種別の参照先を 24 時間記憶。
- 5xx・タイムアウト: 上限付き再試行と減速。成功しなければ品質不足として残し、完了にしない。

---

## 6. データ保存仕様（Storage Spec）

### 6-1. 保存先

| テーブル | 内容 | キー |
|---|---|---|
| `races_ultimate` | レース基本情報（JSON） | `race_id` |
| `race_results_ultimate` | 馬ごとの結果（JSON） | `race_id` + `horse_number` |
| `scraped_dates` | 取得済み日付 | `date` |
| `pedigree_cache` | 血統キャッシュ | `horse_id` |

### 6-2. 保存条件

```
While スクレイプ済みレースを保存する,
when race_data.horses が空のとき,
the system shall 保存をスキップしてログに警告を記録する。

While スクレイプ済みレースを保存する,
when distance = 0 のとき,
the system shall _invalid_distance フラグを true にして保存する（予測時に除外される）。
```

---

## 7. Cloudflare ブロック対応

```
While ページ本文を取得する,
when Cloudflare ブロック HTML を検知したとき（is_cloudflare_block() = true）,
the system shall netkeiba 全体の停止理由を永続化し、以降の自動通信を停止する。
the system shall 保存済みの途中成果を保持し、ジョブを error として理由を表示する。
```

解除は運用者が原因を確認してから行う。再起動・プロキシ切替による自動回避は行わない。

---

## 8. 禁止事項まとめ（Do NOT）

| # | 禁止内容 |
|---|---|
| DN-01 | インターバルを 1.0 秒未満に変更すること |
| DN-02 | `force_rescrape=true` をデフォルトにすること |
| DN-03 | 複数日付を並列（asyncio.gather で複数日）に処理すること |
| DN-04 | `scraped_dates` テーブルを削除・スキップするリロジックを追加すること |
| DN-05 | 401・403・制限画面を無視し、再試行や別ホストへの切替で通信を続けること |
