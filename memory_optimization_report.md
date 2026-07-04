# memory_optimization_report

## DataFrameメモリ最適化結果

| 項目 | 値 |
|---|---|
| before_mb | 2640.74 |
| after_mb | 338.98 |
| reduction_mb | 2301.77 |
| reduction_pct | 87.16 |
| int_downcast_cols | 7 |
| float_downcast_cols | 65 |
| object_to_category_cols | 57 |

適用内容:
- int64 -> pd.to_numeric(..., downcast='integer')
- float64 -> pd.to_numeric(..., downcast='float')
- object -> astype('category') (変換不可列はスキップ)
