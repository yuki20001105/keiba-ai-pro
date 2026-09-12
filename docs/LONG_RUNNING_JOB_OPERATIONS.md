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

現時点では、開始済みのデータ取得ジョブを個別に安全終了するキャンセル機能はありません。

- `stop-keiba-ai-pro.bat` はFastAPIとWeb画面を停止しますが、ジョブをキャンセル済みにはしません。
- 未完了ジョブは永続化されているため、次回起動時に同じjob IDで再開されます。
- ブラウザを閉じる、別画面へ移動する、ブラウザ側の監視を止める操作でも、サーバージョブ自体は停止しません。
- 実行中にジョブDBを直接編集したりPythonプロセスだけを強制終了したりしないでください。保存処理との競合や再開時の二重実行を招く可能性があります。

停止が必要な場合は、現在のジョブが終端状態になるまで新しい取得を開始せず待機します。将来の個別停止は、停止要求を永続化し、ワーカーがレース単位の安全な境界で終了する協調キャンセルとして実装する必要があります。

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
