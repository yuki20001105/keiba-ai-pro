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
| R-SC-04 | システムは HTTP 429（レートリミット）時に指数バックオフで待機する |
| R-SC-05 | システムは取得データを `keiba_ultimate.db` に保存する |
| R-SC-06 | システムは進捗（done/total）をポーリングで返す |
| R-SC-07 | システムは過去 30 日超のデータはカレンダーで開催日を絞り込む |

---

## 2. Specification（仕様）

### 2-1. エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `POST` | `/api/scrape/start` | スクレイプジョブを開始し `job_id` を即時返却 |
| `GET` | `/api/scrape/status/{job_id}` | 進捗を返す（ポーリング用） |
| `GET` | `/api/scrape/jobs` | 全ジョブ一覧 |
| `DELETE` | `/api/scrape/jobs/{job_id}` | ジョブ削除 |

### 2-2. リクエスト

```json
POST /api/scrape/start
{
  "start_date": "20260101",   // YYYYMMDD
  "end_date":   "20260413",   // YYYYMMDD
  "force_rescrape": false     // true = 取得済みをスキップしない
}
```

### 2-3. レスポンス（status）

```json
{
  "job_id": "uuid-string",
  "status": "running | completed | error",
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
[前処理B] SQLite scraped_dates テーブルで取得済み日付を読み込む（レジューム用）
  ↓
[日付ループ] dates[] を 1 件ずつ処理
  ├─ 取得済み → スキップ
  ├─ sleep(_pre_sleep)                          ← 最低 1.0s（INV-07）
  ├─ db.netkeiba.com/race/list/{date}/ → race_id[] 取得
  │    └─ HTTP 400（未開催日）→ スキップ
  │    └─ 0 件 かつ 直近 30 日以内 → race.netkeiba.com/race_list_sub.html にフォールバック
  │         └─ HTTP 429/403/503 → 60s 待機してスキップ
  ├─ [レースループ] race_id ごとに scrape_race_full() を呼び出す
  │    └─ sleep(_inter_race_sleep)              ← 最低 1.0s（INV-07）
  │    └─ scrape_race_full() → race.py 参照
  ├─ _save_race_sqlite_only() で SQLite に保存
  ├─ scraped_dates に記録
  └─ sleep(_post_sleep)                         ← 過去: 2.0s / 直近: 8.0s
```

---

## 4. インターバル仕様（Interval Spec）

> **INV-07 より**: 各ページリクエスト間は最低 **1.0 秒** 必須。

| 変数 | 過去データ（30日超） | 直近データ（30日以内） |
|---|---|---|
| `_pre_sleep`（日付リストページ前） | **1.0s** | 2.0s |
| `_inter_race_sleep`（レース間） | **1.0s** | 2.0s |
| `_post_sleep`（日付間） | 2.0s | 8.0s |
| 馬詳細 4 頭ごと（`race.py`） | **1.0s** | **1.0s** |
| sp.netkeiba 補完（`horse.py`） | **1.0s** | **1.0s** |
| 血統ページ補完（`horse.py`） | **1.0s** | **1.0s** |
| HTTP 429 バックオフ（`race.py`） | `10.0 + attempt * 5.0s` | ← 同左 |

**禁止事項**: 上記の値を 1.0 秒未満に変更すること。

---

## 5. リトライ仕様（Retry Spec）

### race.py — レースページ取得

```
While レースページを取得する,
when HTTP 429 のとき,
the system shall `10.0 + attempt * 5.0` 秒待機して最大 3 回リトライする。

While レースページを取得する,
when HTTP 200 以外（429 を除く）のとき,
the system shall None を返して当該レースをスキップする。

While レースページを取得する,
when タイムアウトまたは例外のとき,
the system shall `2.0 ** attempt` 秒待機して最大 3 回リトライする。
```

### horse.py — 馬詳細ページ取得

```
While 血統ページを取得する,
when HTTP 429 のとき,
the system shall `5.0 + attempt * 3.0` 秒待機する。

While 地方馬（B プレフィックス）の血統を取得する,
when メインページに blood_table がないとき,
the system shall /horse/ped/{horse_id}/ にフォールバックする。
```

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
the system shall None を返し、ログに「SCRAPE_PROXY_URL 環境変数を設定してください」を記録する。
the system shall スクレイプを継続する（致命的エラーにしない）。
```

---

## 8. 禁止事項まとめ（Do NOT）

| # | 禁止内容 |
|---|---|
| DN-01 | インターバルを 1.0 秒未満に変更すること |
| DN-02 | `force_rescrape=true` をデフォルトにすること |
| DN-03 | 複数日付を並列（asyncio.gather で複数日）に処理すること |
| DN-04 | `scraped_dates` テーブルを削除・スキップするリロジックを追加すること |
| DN-05 | Cloudflare ブロック時に例外を raise すること（サイレント続行が正しい） |
