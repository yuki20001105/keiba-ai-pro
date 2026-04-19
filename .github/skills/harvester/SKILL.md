---
name: harvester
description: 'Harvester（ハーベスター）スキル — データ収集・スクレイピング担当。Use when: netkeiba.comからのデータ取得に関する作業 / スクレイピングエラーの調査・修正 / 取得済みデータの確認・DB操作 / スクレイプジョブの管理・進捗確認 / HTMLパーサーの追加・修正 / data-collection / data-view ページの改修。Keywords: スクレイピング, scraping, netkeiba, データ取得, HTMLパーサー, data-collection, data-view, DB保存, scrape_batch, ジョブ管理, レース取得, 払い戻し, entries, results, race_results_ultimate'
---

# Harvester（ハーベスター）— データ収集・スクレイピング

netkeiba.com からレースデータを収集し SQLite に保存するすべての処理を担当。

---

## 担当ページ・ファイル

| 種別 | パス | 役割 |
|---|---|---|
| UI | `src/app/data-collection/page.tsx` | 期間指定一括取得・進捗表示 |
| UI | `src/app/data-view/page.tsx` | 取得済みデータ確認・テーブル表示 |
| API | `python-api/routers/scrape.py` | スクレイプジョブ制御エンドポイント |
| API | `python-api/routers/internal.py` | 内部スクレイプ呼び出し |
| Core | `python-api/scraping/race.py` | HTMLパーサー本体 |
| Core | `python-api/scraping/storage.py` | SQLite書き込み |
| Core | `python-api/scraping/jobs.py` | ジョブキュー・進捗管理 |
| Core | `python-api/scraping/constants.py` | User-Agent・ヘッダー定義 |
| Scheduler | `python-api/scheduler.py` | 定時取得（毎朝6時・2時間おき） |

---

## データフロー

```
netkeiba.com
    │ HTTP GET（1秒以上間隔必須: INV-07）
    ▼
scraping/race.py  ← HTMLパース
    │
    ├─ 出走表ページ (shutuba)  → entries テーブル（レース前）
    └─ 結果ページ (result)    → results テーブル（レース後）
                              → race_results_ultimate テーブル（JSON blob）
                              → return_tables_ultimate（払い戻し）
```

---

## 主要 API エンドポイント

| エンドポイント | 説明 |
|---|---|
| `POST /api/scrape/batch` | 期間指定バッチスクレイプ開始（非同期ジョブ） |
| `GET /api/scrape/status/{jobId}` | ジョブ進捗ポーリング |
| `POST /api/scrape/date-range` | 日付範囲指定スクレイプ |
| `GET /api/races/by-date?date=YYYYMMDD` | 日付別レース一覧 |
| `GET /api/races/recent?limit=50` | 収集済みレース最新50件 |
| `GET /api/data-stats?ultimate=true` | DB統計（レース数・馬数等） |

---

## 不変条件（必ず守ること）

### INV-07: スクレイピングインターバル
```python
# python-api/scraping/race.py
# ❌ 禁止: 1秒未満
await asyncio.sleep(0.5)

# ✅ 必須: 1秒以上
await asyncio.sleep(1.0)   # _pre_sleep, _inter_race_sleep
```

### INV-03: 日付判定
```python
# ❌ 禁止
if race_date < today:  # 当日レースが結果ページを使わない誤動作

# ✅ 正しい
if race_date <= today:  # 当日含む過去レースは結果ページを使用
```

---

## SQLite テーブル（担当範囲）

| テーブル | 内容 | 主キー |
|---|---|---|
| `races` | レース基本情報 | `race_id` |
| `entries` | 出走馬情報（レース前データ） | `(race_id, horse_id)` |
| `results` | レース結果・着順・タイム | `(race_id, horse_id)` |
| `race_results_ultimate` | 結果JSONブロブ（スクレイプ生データ） | `race_id` |
| `races_ultimate` | レース情報JSONブロブ | `race_id` |
| `return_tables_ultimate` | 払い戻し情報 | `race_id` |
| `scraped_dates` | 取得済み日付の記録 | `race_date` |

---

## よくあるエラーと対処

### Cloudflare ブロック（38バイト HTMLが返る）
```
原因: netkeiba のボット検知（IPアドレスブロック）
対処: get_random_headers() を使用・1秒以上待機
※ IP自体がブロックされた場合は対処不可（サイレント続行）
```

### HTMLパース失敗（NoneType エラー）
```python
# scraping/race.py でのパース時はすべて .get() か try-except で保護
# CSS セレクタが変わった場合はセレクタを更新
```

### ジョブが進まない / タイムアウト
```
原因: GIL競合またはネットワーク遅延
確認: GET /api/scrape/status/{jobId} でステータス確認
対処: ジョブをキャンセルして再実行
```

---

## ydata-profiling（プロファイリングレポート）

スクレイプ後に特徴量品質を確認するためのレポートを生成できる。

```
POST /api/profiling          ← 生成開始
GET  /api/profiling/status/{jobId}  ← 進捗確認
```

生成されたレポートは Trainer エージェントが解析して特徴量最適化に使用する。  
→ 詳細は `feature-profiling-analysis` スキルを参照。

---

## 開発時の確認コマンド

```powershell
# DB の races テーブル件数確認
& ".venv\Scripts\python.exe" -c "
import sqlite3, sys; sys.path.insert(0,'python-api')
from app_config import ULTIMATE_DB
conn = sqlite3.connect(str(ULTIMATE_DB))
print('races:', conn.execute('SELECT COUNT(*) FROM races').fetchone()[0])
print('entries:', conn.execute('SELECT COUNT(*) FROM entries').fetchone()[0])
print('results:', conn.execute('SELECT COUNT(*) FROM results').fetchone()[0])
conn.close()
"
```
