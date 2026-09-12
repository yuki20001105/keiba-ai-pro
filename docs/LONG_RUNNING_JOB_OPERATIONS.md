# 長期ジョブ運用

## 結論

新しく開始するデータ取得ジョブは、FastAPIの再起動後も同じjob IDで自動再開されます。進捗、入力期間、完了日、heartbeat、再開回数は `keiba/data/scrape_jobs.db` に保存されます。

ローカルランチャーはNext.jsを既定でstandalone production modeで起動します。開発用ファイル監視を長時間動かさないため、メモリとファイルハンドルの増加を抑えます。

## 利用方法

1. `start-keiba-ai-pro.bat` を実行します。
2. 画面からデータ取得を開始します。
3. 稼働確認は `check-keiba-ai-pro.bat` を実行します。

正常時の表示例:

```text
[LONG JOB] auto-resume=True, active=1, recoverable=1, stale=0
[RESOURCE] FastAPI RSS=... MB / system available=... MB
[FRONTEND] mode=production
```

- `active`: 現在動作している取得ワーカー数
- `recoverable`: 再起動後も復元できる未完了ジョブ数
- `stale`: heartbeatが一定時間更新されていないジョブ数。0が正常
- `frontendMode`: 長時間利用では `production` を推奨

## 再起動時の動作

- 完了済みの日付は再取得しません。
- 処理途中の日は再度実行します。SQLite保存は同一race IDを上書きするため、途中再実行で重複を増やしません。
- 一時的に取得結果が0件だった日は「開催なし」と確定せず、次回の再試行対象に残します。
- 旧バージョンで開始され、期間設定がDBに保存されていないジョブは安全に復元できないため、`error` のままです。今回の更新後に開始したジョブが自動再開対象です。

## プロセス停止とジョブキャンセル

データ取得画面の実行中カードから、対象jobだけを安全に停止できます。

1. 対象期間とjob IDを確認します。
2. 「取得を停止」または「Dry-runを停止」を押します。
3. 確認メッセージの内容を確認して続行します。
4. 表示が `停止中` から `停止しました` に変わるまで待ちます。画面は状態を自動再確認します。

- 待機中（`queued`）のjobは、停止要求の保存後にそのまま `cancelled` になります。
- 実行中（`running`）のjobは `cancelling` になり、現在のレースに関係する保存処理を一貫した区切りまで終えてから `cancelled` になります。
- 停止前に保存済みのレースデータは削除しません。未処理期間が必要な場合は、停止完了後にあらためて取得します。
- 同じ停止操作を再送しても、別jobを停止したり停止処理を二重実行したりしません。
- 完了と停止要求が競合し、取得処理が先に完了していた場合は `completed` を正しい終端状態として扱います。
- 停止要求は永続化されるため、停止処理中にプロセスが再起動しても、同じjobを通常実行として再開しません。
- `stop-keiba-ai-pro.bat` はFastAPIとWeb画面のプロセス停止であり、個別jobのキャンセル操作ではありません。
- ブラウザを閉じる、別画面へ移動する、ブラウザ側の監視だけを止める操作でも、サーバージョブ自体は停止しません。
- 実行中にジョブDBを直接編集したりPythonプロセスだけを強制終了したりしないでください。保存処理との競合や再開時の二重実行を招く可能性があります。

停止APIはjob所有者に限定され、管理操作の本人確認後にのみ実行できます。停止要求の応答を確認できない場合、画面はjobが継続中の可能性を前提にロックを維持し、自動再確認を続けます。

## 資源ガード

既定値は次のとおりです。

| 環境変数 | 既定値 | 動作 |
|---|---:|---|
| `SCRAPE_MAX_PROCESS_RSS_MB` | 8192 | FastAPI RSSが上限以上なら待機 |
| `SCRAPE_MIN_AVAILABLE_MB` | 1024 | システム空きメモリが下限以下なら待機 |
| `SCRAPE_RESOURCE_POLL_SEC` | 30 | 資源回復の確認間隔 |
| `SCRAPE_HEARTBEAT_INTERVAL_SEC` | 10 | 通常処理中のheartbeat保存間隔 |
| `SCRAPE_STALE_HEARTBEAT_SEC` | 180 | staleと判定するまでの秒数 |
| `SCRAPE_SUPERVISOR_INTERVAL_SEC` | 60 | 自動復旧監視間隔 |

資源不足時はジョブを失敗させず `waiting_resources` にして待機し、空きが戻ると自動継続します。

## 開発モードが必要な場合

画面実装を変更しながら確認する場合だけ、次を使用します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local-app.ps1 -DevelopmentFrontend
```

`next dev` はファイル監視と開発キャッシュにより長時間稼働の資源消費が大きいため、通常利用や長期取得には使用しません。

## 異常判定

次のいずれかなら調査が必要です。

- `/health` の `status` が `degraded`
- `stale_job_count` が1以上
- `resource_capacity_available` がfalse
- `check-keiba-ai-pro.bat` でFastAPIまたはNext.jsがNG

ログは `logs/local-app/fastapi.stderr.log` と `logs/local-app/next.stderr.log` を確認します。保存済みレースデータはジョブ状態DBとは別の `keiba/data/keiba_ultimate.db` にあり、画面更新やFastAPI再起動では削除されません。
