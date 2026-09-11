# 研究DBの復元基準

更新日: 2026-09-11

## 結論

`keiba/data/keiba_ultimate.db`はGit管理やゼロからの再スクレイプではなく、検証済みのローカル原本から復元します。復元時は原本を直接変更せず、必ずコピーを作成して研究専用データを除去・検証した後に切り替えます。

## 現在のDB

- パス: `keiba/data/keiba_ultimate.db`
- サイズ: 2,060,521,472 bytes
- SHA-256: `7D59630CA78B3CC01D9E309EC86DE3E363028B17029A21B22A2FC04A1C107EFF`
- SQLite `quick_check`: `ok`
- データ期間: 2013-01-01〜2026-07-11

主要件数:

| テーブル | 件数 |
|---|---:|
| `races_ultimate` | 52,593 |
| `race_results_ultimate` | 577,110 |
| `return_tables_ultimate` | 584,086 |
| `scraped_dates` | 800 |
| `holding_times_cache` | 23,969 |
| `training_data` | 85,040 |
| `speed_figures` | 15,560 |
| `prediction_log` | 3,458 |

外部キー違反、孤児結果、主キー重複は0件です。sandbox 3表と追加品質4表は空、`official`または`licensed`を名前に含むテーブルはありません。

## 優先する復元元

```text
%USERPROFILE%\Documents\keiba-authorized-export\research\keiba_ultimate_jra_official.db
```

- サイズ: 2,568,048,640 bytes
- SHA-256: `6F487D2876B6F1A9CFE31029EB339C611EEBA8A750840252A370E32B6638BC7E`

この原本は旧DBの厳密な上位集合です。現在の研究DBは、この原本のコピーから次を除去して作成しました。

- 研究用途に不要な`official_history_entries`と`official_history_imports`
- sandbox 3表のスモークテスト行
- `race_results_ultimate`の孤立した`SMK_CANARY_*`行

その後に`VACUUM`、`quick_check`、件数・孤児・重複・日付範囲の監査を行っています。

## 旧世代のフォールバック

```text
%USERPROFILE%\OneDrive\デスクトップ\keiba_ultimate.db
```

- サイズ: 1,992,540,160 bytes
- SHA-256: `986F42921F65192590DDD8F65E52EC7AE3731D6FA1E49FE39B403CD6E65036B8`

旧世代原本は現DBに全行が含まれることを確認済みです。優先原本が使えない場合の復旧アンカーとして保持します。

## 安全な復元手順

1. 復元元の存在、サイズ、SHA-256を上記の値と照合する。
2. SQLite Backup API等で別名の一時DBへコピーし、原本を変更しない。
3. 研究DBに含めない表・スモーク行をコピー側だけから除去する。
4. `VACUUM`後、`PRAGMA quick_check`、外部キー、孤児、主キー重複、主要件数、日付範囲を確認する。
5. APIやNotebookを停止した状態で、一時DBを`keiba/data/keiba_ultimate.db`へ同一ボリューム上で切り替える。
6. 実ローダー`load_ultimate_training_frame`の読み取りスモークをUTF-8モードで行う。

現在のDBに対応する`-wal`と`-shm`はSQLiteが管理するため、手動削除しません。中間復元DBを長期保持する必要はなく、復元元のハッシュと監査結果を記録してから削除します。
