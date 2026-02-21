# tools/ — データ整備・メンテナンスツール

本番 DB の**データ補完・メンテナンス**のために使うスクリプト群です。

## スクリプト一覧

| ファイル | 用途 | 実行頻度 |
|---|---|---|
| `../patch_missing_data.py` | DB の欠損フィールドを netkeiba からスクレイピングして補完 | データ収集後・必要時 |

> `patch_missing_data.py` はルートに置いてあります（実行中は移動不可のため）

## logs/

パッチ実行時のログが出力されます。

- `patch_log.txt` — 標準出力（進捗）
- `patch_log_err.txt` — エラー出力

## patch_missing_data.py の使い方

```powershell
# 確認のみ（DBは更新しない）
.venv\Scripts\python.exe patch_missing_data.py --dry-run

# Phase 指定実行（3=前走情報補完）
.venv\Scripts\python.exe patch_missing_data.py --phase 3

# 全フェーズ実行（バックグラウンド）
Start-Process .venv\Scripts\python.exe -ArgumentList "patch_missing_data.py" `
  -RedirectStandardOutput tools\logs\patch_log.txt `
  -RedirectStandardError tools\logs\patch_log_err.txt
```

### フェーズ説明

| Phase | 対象 | 内容 |
|---|---|---|
| 0 | `races_ultimate` | レース名・天気・開催情報補完 |
| 1 | `race_results_ultimate` | 馬名・馬番・騎手など基本情報補完 |
| 2 | `race_results_ultimate` | 血統情報（sire/dam/damsire）補完 |
| 3 | `race_results_ultimate` | 前走情報（prev_race_date/venue/finish）補完（日付フィルタ付き） |
