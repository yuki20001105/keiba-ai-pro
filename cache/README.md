# cache

Expected cache files used by notebook audit pipeline:

- ultimate_frame.parquet
- features.parquet
- predictions.parquet
- race_results.parquet
- horse_history.parquet
- training_data.parquet

Cache keys:

- data_version
- feature_schema_hash
- notebook_step
- mode

Invalidation triggers:

- schema change
- DB update
- mode change
