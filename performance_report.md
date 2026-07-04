# performance_report

## 処理時間比較（秒）

| 処理 | 時間(sec) |
|---|---|
| load_ultimate_training_frame (baseline) | 52.826 |
| load_ultimate_training_frame_cached (miss) | 0.235 |
| load_ultimate_training_frame_cached (hit) | 0.202 |
| add_derived_features (sample=20,000 rows) | 166.737 |

## load_ultimate_training_frame ステージ内訳（秒）

| ステージ | 時間(sec) |
|---|---|
| sqlite_read | 7.3394 |
| merge | 9.7949 |
| astype | 0.5776 |
| fillna | 0.2079 |
| sort | 4.0801 |
| feature_creation | 14.0829 |
| memory_opt | 0.0 |

## Polars化可能性（簡易ベンチ）

| 実装 | 時間(sec) | RSS増分(GB) |
|---|---|---|
| pandas | 0.169 | 0.035 |

Polarsは未インストールまたは実行不可でした。
- reason: ModuleNotFoundError: No module named 'polars'

## 目標との差分

- load_ultimate_training_frame 目標 <= 10 sec: 実測 52.826 sec
- RSS 目標 <= 2 GB: 実測 after 5.194 GB

raw metrics: C:/Users/yuki2/Documents/ws/keiba-ai-pro/docs/reports/performance_metrics.json
