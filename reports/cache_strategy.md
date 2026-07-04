# cache_strategy

## 中間キャッシュ方針

1. cache/ 以下に Notebook監査専用のparquetキャッシュを保持
2. キー不一致時はキャッシュを無効化して再生成
3. それ以外はキャッシュを優先利用

対象ファイル:
- cache/ultimate_frame.parquet
- cache/features.parquet
- cache/predictions.parquet
- cache/race_results.parquet
- cache/horse_history.parquet
- cache/training_data.parquet

キャッシュキー:
- data_version
- feature_schema_hash
- notebook_step
- mode

invalidation:
- schema変更
- DB更新
- mode変更

## 実測

| 項目 | 値 |
|---|---|
| cache_miss_sec | 0.235 |
| cache_hit_sec | 0.202 |
| parquet_exists | False |
| pickle_exists | False |

## SQLite高速化（実施結果）

| 項目 | 値 |
|---|---|
| create_index_sec | 0.0 |
| analyze_sec | 0.517 |
| vacuum_sec | 27.919 |
| created_index_count | 0 |

必須インデックス存在確認:
- race_id: True
- horse_id: True
- jockey_id: True
- trainer_id: True
- date: True
