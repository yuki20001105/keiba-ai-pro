# netkeiba × 継続学習「競馬AI」ひな形（Python）

**重要（必ず読んでください）**
- netkeiba はスクレイピングによる多数アクセスを検知した場合、**予告なく通信制限**することがあると案内しています。解除依頼しても解除できない場合がある旨も記載されています。  
  （実運用では、取得頻度を落とす・キャッシュする・必要最小限にする等、負荷を最小化してください）
- 本プロジェクトは「研究・個人学習向けの最小構成」です。**ブロック回避（IPローテーション等）や対策突破は実装していません**。

## できること（このリポジトリで提供）
1. 指定日のレース一覧から `race_id` を抽出（`race_list.html?kaisai_date=YYYYMMDD`）
2. `race_id` ごとに
   - 出馬表（`/race/shutuba.html?race_id=...`）
   - 結果（`/race/result.html?race_id=...`）
   を取得し、HTMLをキャッシュしたうえで **SQLiteに保存**
3. SQLiteの蓄積データから、まずはベースラインの **ロジスティック回帰モデル**を学習
4. 指定レース `race_id` の出馬表から **簡易スコア（p_win_like）** を出力

> まず「動く」ことを最優先にしてあります。精度を上げるなら「過去走特徴」「コース適性」「騎手/調教師成績」「ラップ」などを追加してください。

## セットアップ（Windows / Linux 共通）
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

## 使い方

### 1) 指定日の race_id を取得
```bash
python -m keiba_ai.ingest --config config.yaml date 20260104
```

### 2) race_id を1つ取り込む（出馬表+結果）
```bash
python -m keiba_ai.ingest --config config.yaml race 202506050811
```

### 3) まとめて日次実行（date -> ingest -> train）
```bash
python -m keiba_ai.pipeline_daily --config config.yaml --date 20260104
```

### 4) 予測（出馬表から上位を表示）
```bash
python -m keiba_ai.predict --config config.yaml --model data/models/model_win_YYYYMMDD_HHMMSS.joblib --race_id 202606010107 --topk 8
```

## 継続運用（例）
- **毎日 18:00** に `pipeline_daily` を実行（Windows タスクスケジューラ / cron）
- 取得は **当日分 or 前日分** に限定（過去を一気に取るとアクセスが増えます）
- `config.yaml` の `min_sleep_sec/max_sleep_sec/max_pages_per_run` を安全側に調整

## よくある詰まりポイント
- `UnicodeDecodeError` / 文字化け: `client.py` で `apparent_encoding` を見て decode するようにしていますが、ページ側仕様が変わると調整が必要です。
- `pd.read_html` が空: ページが動的化/仕様変更している可能性があります（その場合は **無理に突破せず**、データソースの見直しを推奨します）


## UI（Streamlit）
### 起動
```bash
pip install -r requirements.txt
streamlit run ui_app.py
```
Windows の場合は `run_ui.bat` をダブルクリックでも起動できます。

### ページ構成
- 1_データ取得：日付→race_id一覧→選択取り込み
- 2_学習：SQLiteから学習（ベースライン）
- 3_予測：モデル×出馬表で上位表示（CSV DL）
- 4_DB確認：取り込み状況と未取得チェック
