---
name: jobs
description: 'オーケストレーター（ジョブズ）スキル。Use when: 複数領域にまたがるタスクを整理・分割したい / どのエージェントに依頼すればよいか判断したい / 競馬AIシステム全体の改善計画・ロードマップを考えたい / 複数のスキルを組み合わせて進める必要があるとき / 初めてシステムに触れる場合の全体案内が必要なとき。Keywords: 全体設計, ロードマップ, 計画, エージェント振り分け, 優先順位, jobs, orchestrator, どこに頼む, 何をすべきか'
---

# Jobs（ジョブズ）— オーケストレーター

keiba-ai-pro 全体を俯瞰し、タスクを適切なエージェントに振り分けるオーケストレータースキル。

---

## エージェント全体マップ

```
                        ┌─────────────┐
                        │    Jobs     │  ← あなたは今ここ
                        │ （全体統括） │
                        └──────┬──────┘
           ┌──────────┬────────┼────────┬──────────┐
           ▼          ▼        ▼        ▼          ▼
      Harvester    Trainer   Oracle   Ledger     Sysop
     （データ収集）（学習）  （予測）  （損益管理） （基盤）
```

---

## エージェント振り分けガイド

| ユーザーの依頼・キーワード | 担当エージェント |
|---|---|
| スクレイピング / データ取得 / netkeiba / DB保存 / 収集エラー | **Harvester** |
| モデル学習 / Optuna / 特徴量 / AUC / 過学習 / プロファイリング | **Trainer** |
| 予測エラー / analyze_race / Kelly / 買い目 / オッズ取得 | **Oracle** |
| 購入履歴 / 的中率 / 回収率 / 損益 / ダッシュボード | **Ledger** |
| git / デプロイ / 認証 / スケジューラ / 環境設定 / テスト | **Sysop** |
| 上記複数にまたがる / 全体改善計画 | **Jobs（自分）** |

---

## システム全体フロー（4ステップ）

```
Step 01: データ取得
  → Harvester が担当
  → netkeiba.comをスクレイプ → SQLite (keiba_ultimate.db) に保存

Step 02: モデル学習
  → Trainer が担当
  → SQLite のデータを読み込み → 特徴量生成 → LightGBM + Optuna で学習

Step 03: 予測実行
  → Oracle が担当
  → 学習済みモデルをロード → レースデータ取得 → 勝率予測 → Kelly推奨

Step 04: 成績確認
  → Ledger が担当
  → 購入記録管理 → 損益計算 → 的中率・回収率分析
```

---

## 不変条件（INV）— 全エージェント共通

どのエージェントが作業しても、以下は絶対に守ること:

| ID | 内容 |
|---|---|
| **INV-01** | 予測パイプライン順序を変更しない: `DB読み込み→特徴量生成→POST_RACE_FIELDS除外→LightGBM→Kelly` |
| **INV-02** | オッズ判定は `is not None` を使う（`or` 演算子による falsy 判定禁止） |
| **INV-03** | 日付判定は `race_date <= today`（当日含む）。`< today` は禁止 |
| **INV-04** | `CONCURRENCY = 1` を維持（GIL競合防止） |
| **INV-05** | UI→API: 180,000ms以上、Next.js→FastAPI: 300,000ms以上 |
| **INV-07** | スクレイピング間隔は最低1.0秒 |

詳細は `docs/specs/SYSTEM.md` を参照。

---

## コードベース構造

```
keiba-ai-pro/
├── src/                   # Next.js (UI + APIプロキシ)
│   └── app/
│       ├── home/          # ホーム（認証後ハブ）
│       ├── data-collection/   # Harvester 担当
│       ├── data-view/         # Harvester 担当
│       ├── train/             # Trainer 担当
│       ├── feature-lab/       # Trainer 担当
│       ├── race-analysis/     # Oracle 担当
│       ├── predict-batch/     # Oracle 担当
│       ├── prediction-history/# Ledger 担当
│       └── dashboard/         # Ledger 担当
├── python-api/            # FastAPI バックエンド
│   ├── routers/           # エンドポイント実装
│   ├── scraping/          # Harvester 担当
│   └── training/          # Trainer 担当
└── keiba/
    ├── keiba_ai/          # コアロジック（全エージェント共通）
    └── data/
        └── keiba_ultimate.db  # 主データストア
```

---

## 複数エージェントにまたがるタスク例

### 例1: 予測精度を改善したい
```
1. Trainer → 特徴量プロファイリング・不要特徴量除去
2. Trainer → 再学習（Optuna 100回）
3. Oracle  → 新モデルで予測テスト・エラー確認
4. Ledger  → 的中率変化を確認
```

### 例2: 新しいレース場を追加スクレイプしたい
```
1. Harvester → scraping/race.py のパーサー確認・追加
2. Harvester → data-collection で取得テスト
3. Trainer   → 新データで再学習
4. Oracle    → 新レース場のレースで予測テスト
```

### 例3: 本番リリースしたい
```
1. Sysop → develop → main → release のマージフロー
2. Sysop → タグ打ち・Railway/Renderへのデプロイ確認
3. Oracle → 本番環境で予測動作確認
```

---

## 現在のシステム状態（スナップショット）

- **主モデル**: `speed_deviation_lightgbm_*`（速度偏差ターゲット）
- **DB**: `keiba/data/keiba_ultimate.db`（prediction_log: 537件）
- **最新モデルID**: `20260418_1928`
- **スケジューラ**: 毎朝6時（前日結果取り込み）+ 日中2時間おき（当日データ）
