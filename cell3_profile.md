# cell3_profile

## 対象

- notebook: 02_data_validation.ipynb
- target cell: Cell 3 (データ読み込み)
- code path: load_ultimate_training_frame(keiba/data/keiba_ultimate.db)

## 実測結果（ノートブック外の直接プロファイル）

- memory_before_gb: 0.064
- memory_after_gb: 5.188
- elapsed_sec: 33.7
- shape: (575346, 132)
- dataframe_memory_mb(deep): 2640.74
- dtypes: float64=65, object=60, int64=7
- object_cols: 60

## ノートブック実行時の観測

- strict timeout run (cell_timeout=30, notebook_timeout=120):
  - status: timeout
  - retry: 3 (attempt 1-4)
  - error: [soft-timeout] cell_timeout exceeded at cell 3
  - output files:
    - notebook_execution_log.csv
    - notebook_execution_report.md
    - notebook_execution_trace.jsonl
- trace heartbeat:
  - cpu_percentが複数回 20-38% で推移
  - last_cell_number=3 で活動が継続
  - 監視ロジック上は hard-timeout ではなく soft-timeout 判定

- production timeout probe (cell_timeout=600, notebook_timeout=7200):
  - trace上で Cell3 は `status=ok`（elapsed_sec=38.61）
  - Cell4〜Cell11 へ進行を確認
  - したがって Cell3 は「600秒でも完了しないハング」ではない
  - ただし実行全体は長時間化のため、最終完了前に手動停止

## 判定

Cell 3 は「ハング（完全停止）」よりも「長時間処理 + 高メモリ圧迫」による timeout である可能性が高い。

根拠:

- 直接実行で 33.7 秒を要し、cell_timeout=30 秒を超過する
- DataFrame の deep memory が約 2.64 GB、プロセス RSS が約 5.19 GB まで増加
- heartbeat で CPU 活動が継続しており、完全無応答の兆候が弱い

補足:

- Windows + ipykernel/pyzmq 周辺で `zmq.error.ZMQError: not a socket` が間欠的に発生しており、再試行失敗を増幅する要因になりうる
- ただし本件 Cell 3 timeout の一次要因は、重い読み込み処理そのもの

## Option比較（依頼の Option1-5）

1. Cell分割
- 効果: 低〜中（根本負荷は同じ）
- メリット: 進捗可視化がしやすい
- デメリット: 実時間短縮は限定的

2. キャッシュ（中間結果保持）
- 効果: 高（再実行で大幅短縮）
- メリット: 反復デバッグが速い
- デメリット: キャッシュ無効化ルールが必要

3. parquet/pickle への事前変換
- 効果: 高（SQLite再組立コスト削減）
- メリット: 読み込み高速化、型安定化
- デメリット: 生成フローの整備が必要

4. sqlite index 強化
- 効果: 中（クエリ次第）
- メリット: DB取得時間を改善可能
- デメリット: 既存クエリに効かない場合は効果薄

5. polars 置換
- 効果: 中〜高（処理内容次第）
- メリット: メモリ効率/速度改善余地
- デメリット: 既存pandasコードの互換修正コスト大

## 推奨順

1. Option 3 (parquet/pickle 事前変換)
2. Option 2 (キャッシュ)
3. Option 4 (sqlite index)
4. Option 1 (Cell分割)
5. Option 5 (polars 置換)

## 暫定運用

- notebook監査の実行時は cell_timeout を 60 秒以上（推奨 120 秒）に上げる
- notebook_timeout は 7200 秒維持
- 02 notebook を単独実行して安定化確認後に 00-08 通し実行へ戻す
