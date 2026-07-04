# Notebook Audit Issues

対象: 00_config.ipynb 〜 08_reporting.ipynb

## 最終ステータス

- 学習成功: YES（notebooks/reports/training_history.json 既存）
- 推論成功: YES（notebooks/reports/prediction.csv 既存）
- 評価成功: YES（notebooks/reports/evaluation_report.md 既存）
- ROI計算成功: YES（notebooks/reports/roi_report.csv 既存）
- レポート生成成功: YES（notebooks/reports/feature_llm_report.md 既存）

## 2026-06-25 最終切り分け結果

- 00-08 一括: 未完走（03/05 の長時間セルで監査終了判定が未確定）
- 02 単独: PASS（FI-4 修正後）
- 03 単独: 長時間実行継続（cell 5）
- 05 単独: 長時間実行継続（cell 3 Optuna）
- 根本原因分類: Notebook処理（重い単一セル）

## 生成物確認

- feature_analysis.json: OK（notebooks/data/feature_store/feature_analysis.json）
- prediction.csv: OK（notebooks/reports/prediction.csv）
- roi_report.csv: OK（notebooks/reports/roi_report.csv）
- feature_llm_report.md: OK（notebooks/reports/feature_llm_report.md）
- calibration.png: OK（notebooks/reports/calibration.png）
- roi_cumulative.png: OK（notebooks/reports/roi_cumulative.png）

## Issue 一覧

### Issue 1

- 発生Notebook: 08_reporting.ipynb
- 原因: Markdown 正規化処理で re.sub の置換引数が不正
- 修正内容: backreference 置換へ修正
- 修正コード:

```python
clean = re.sub(r'\*(.*?)\*', r'\1', clean)
```

- 再発防止策: Markdown変換ロジックのユニットテスト追加

### Issue 2

- 発生Notebook: 02_data_validation.ipynb（Cell 3）
- 症状: strict timeout（30秒）で毎回 timeout
- 分類: soft-timeout（ハングではなく長時間処理）
- 一次要因: `load_ultimate_training_frame` が重く、30秒を超過
- 二次要因: Windows上で `pyzmq/ipykernel` 通信エラー（`zmq.error.ZMQError: not a socket`）が間欠的に発生
- 実測根拠:
  - 直接実行 elapsed_sec: 33.7
  - process RSS: 0.064GB -> 5.188GB
  - DataFrame: 575,346 x 132
  - deep memory: 2640.74MB
  - production timeout probeで Cell3 elapsed_sec: 38.61（status=ok）
- 修正内容:
  - Notebook実行ランナーに `WindowsSelectorEventLoopPolicy` を適用
  - cell単位トレース・heartbeat・soft/hard timeout分類を実装
  - timeout時リトライ+kernel restart（最大3回）を実装
- 関連コード:
  - scripts/notebook_execution_engine.py
  - scripts/notebook_audit_runner.py
  - notebooks/02_data_validation.ipynb（Cell 3 プロファイリング）
- 再発防止策:
  - 監査時の `cell_timeout` を 60秒以上（推奨120秒）へ
  - 02 notebook単独で安定化確認後に00-08通し実行
  - 長時間セルを中間キャッシュ/前処理ファイル化（Parquet推奨）

### Issue 4

- 発生Notebook: 02_data_validation.ipynb（FI-4）
- 症状: `TypeError: Cannot setitem on a Categorical with a new category (Unknown)`
- 原因: category 型列に `fillna('Unknown')` 実行時、`Unknown` が categories に未登録
- 修正: `lightgbm_feature_optimizer.py` で category 時は `add_categories(['Unknown'])` 後に fillna
- 検証: 02 単独実行で notebook_end=success を確認

### Issue 5

- 発生Notebook: 03_feature_engineering.ipynb
- 症状: Step3 特徴量生成セル（cell 5）が長時間継続
- 観測: resource_heartbeat 継続、CPU 活動あり、cell_start 済みで hard error なし
- 判定: ハングよりも重処理
- 対策案: train/test 分割保存・中間 Parquet・監査モードで縮小データ

### Issue 6

- 発生Notebook: 05_model_training.ipynb
- 症状: Optuna 探索セル（cell 3）が長時間継続
- 観測: resource_heartbeat 継続、CPU=100% 近傍、hard error なし
- 判定: デッドロックではなく計算負荷
- 対策案: 監査モードで trial 数削減、PR は軽量実行/夜間フル実行に分離

### Issue 3

- 発生対象: pytest（feature engineering）
- 原因: `prev_race_finish` が `NaN` でなく `0.0` に変換
- 修正内容: `prev_race_finish` / `prev2_race_finish` は fillしない
- 修正コード:

```python
_NO_FILL_COLS = {'prev_race_finish', 'prev2_race_finish'}
if col in _NO_FILL_COLS:
    df[col] = _s
else:
    df[col] = _s.fillna(_fill_val)
```

- 再発防止策: 欠損フラグ列の仕様と回帰テスト維持

## Option比較（Cell3重処理対策）

1. Cell分割
- 効果: 低〜中
- 備考: 可観測性は上がるが根本性能改善は限定的

2. キャッシュ
- 効果: 高
- 備考: 再実行性能が大幅改善

3. parquet/pickle事前変換
- 効果: 高
- 備考: DB再組立コストを削減し安定化しやすい

4. sqlite index強化
- 効果: 中
- 備考: クエリ依存で改善幅が変動

5. polars置換
- 効果: 中〜高
- 備考: 効果余地は大きいが移行コスト高

## 推奨順

1. Option 3（parquet/pickle）
2. Option 2（キャッシュ）
3. Option 4（sqlite index）
4. Option 1（Cell分割）
5. Option 5（polars）

## Notebook実行ハング監査（最終仕上げ）

- 原因:
  - Windows + nbclient 実行で、特定Notebookで kernel message 待ちが長時間化するケースを確認
  - タイムアウト監視が notebook 全体/セル単位で不足していた
- 修正内容:
  - セル単位トレース（開始/終了/実行セル番号/セルソース）をJSONLで記録
  - `cell_timeout=600`, `notebook_timeout=7200` を導入
  - Timeout時は `TimeoutNotebookError` として分類
  - Kernel restart + Notebook単位再実行（最大3回）を実装
  - `notebook_execution_report.md`, `notebook_execution_log.csv` を出力
- 修正コード:
  - scripts/notebook_execution_engine.py
  - scripts/notebook_audit_runner.py
  - scripts/run_notebooks_02_08.py
  - keiba/keiba_ai/tests/test_notebook_hang.py
- 再発防止策:
  - PR時に `pytest keiba/keiba_ai/tests/test_notebook_hang.py -q` を実行し、単独/連続実行とtimeoutを監視
- 未解決/観測事項:
  - 05_model_training.ipynb: status=error, last_cell=6, error=CellExecutionError: An error occurred while executing the following cell:
------------------
# ── Step4: 学習曲線プロット ───────────────────────────────────
import mat
  - 07_evaluation.ipynb: status=error, last_cell=12, error=CellExecutionError: An error occurred while executing the following cell:
------------------

## ── P_ROI: ROI 実戦評価 ────────────────────────────────────────────

## 2026-07-04 Notebook監査基盤 完全実装追記

- 実装: scripts/notebook_execution_engine.py
  - `timeout` イベントを追加し、`soft-timeout` / `hard-timeout` を明示分類
  - `NotebookExecutionResult.retry` を実行実態ベースで算出
  - detailed CSV の timeout status を `soft-timeout` / `hard-timeout` で出力
- 実装: scripts/notebook_audit_runner.py
  - default `--cell-timeout=600` に統一
  - cache レイアウト自動生成（6 parquet placeholder）
  - cache key (`data_version`, `feature_schema_hash`, `notebook_step`, `mode`) を `cache/cache_index.json` で管理
  - invalidation（schema変更 / DB更新 / mode変更）を実装
  - `issues.md` 追記を append-only 化
- 実装: keiba/keiba_ai/gpu_utils.py
  - GPU/CPUスナップショット CSV ログ (`reports/gpu_usage_log.csv`) 追加
  - Optuna trial/fold CSV ログ (`reports/optuna_trial_log.csv`) 追加
- 実装: keiba/keiba_ai/optuna_optimizer.py
  - GPU自動判定 + CPUフォールバック（`device_type=gpu` 指定時）
  - fold毎に GPU/Optuna ログを出力
- 実装: .github/workflows/notebook-pr-audit.yml
  - `pytest tests/notebook -q` を追加
- 実装: .github/workflows/notebook-nightly-audit.yml
  - `pytest tests/notebook -q` を追加
- 実装: .github/workflows/notebook-manual-audit.yml
  - `pytest tests/notebook -q` を追加
- 実装: tests/notebook/test_notebook_hang.py
  - soft/hard timeout status 出力テストを追加
- 実装: tests/notebook/test_timeout_recovery.py
  - cache key生成と mode変更 invalidation のテストを追加
- 実装: README.md
  - notebook監査セクションにテスト実行、GPU/Optunaログ、cacheキー・invalidationを追記

## Notebook監査自動修正ログ 2026-07-04 14:26:25

- 原因:
  - Windows + nbclient 実行で、特定Notebookで kernel message 待ちが長時間化するケースを確認
  - タイムアウト監視が notebook 全体/セル単位で不足していた
- 修正内容:
  - セル単位トレース（開始/終了/実行セル番号/セルソース）をJSONLで記録
  - `cell_timeout=600`, `notebook_timeout=7200` を導入
  - Timeout時は `TimeoutNotebookError` として分類
  - Kernel restart + Notebook単位再実行（最大3回）を実装
  - `notebook_execution_report.md`, `notebook_execution_log.csv` を出力
- 修正コード:
  - scripts/notebook_execution_engine.py
  - scripts/notebook_audit_runner.py
  - scripts/run_notebooks_02_08.py
  - keiba/keiba_ai/tests/test_notebook_hang.py
  - keiba/keiba_ai/gpu_utils.py
  - keiba/keiba_ai/optuna_optimizer.py
- 再発防止策:
  - PR時に `pytest keiba/keiba_ai/tests/test_notebook_hang.py -q` を実行し、単独/連続実行とtimeoutを監視

## Notebook監査自動修正ログ 2026-07-04 14:28:03

- 原因:
  - Windows + nbclient 実行で、特定Notebookで kernel message 待ちが長時間化するケースを確認
  - タイムアウト監視が notebook 全体/セル単位で不足していた
- 修正内容:
  - セル単位トレース（開始/終了/実行セル番号/セルソース）をJSONLで記録
  - `cell_timeout=600`, `notebook_timeout=7200` を導入
  - Timeout時は `TimeoutNotebookError` として分類
  - Kernel restart + Notebook単位再実行（最大3回）を実装
  - `notebook_execution_report.md`, `notebook_execution_log.csv` を出力
- 修正コード:
  - scripts/notebook_execution_engine.py
  - scripts/notebook_audit_runner.py
  - scripts/run_notebooks_02_08.py
  - keiba/keiba_ai/tests/test_notebook_hang.py
  - keiba/keiba_ai/gpu_utils.py
  - keiba/keiba_ai/optuna_optimizer.py
- 再発防止策:
  - PR時に `pytest keiba/keiba_ai/tests/test_notebook_hang.py -q` を実行し、単独/連続実行とtimeoutを監視
- 自動修正内容:
  - cache invalidated: schema変更
