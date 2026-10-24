# 固定 H04 前向き収集の再起動対策

既存の `h04-live-20260920-v2` のモデル・99特徴量・校正器・購入ルール・protocol・analysis definition・収集ソースは変更しない。外側の起動補助だけを追加した。

## 登録済みタスク

- 名前: `KeibaAI-Forward-H04-20260920-v2-Resume`
- 現在のユーザーがログオンした時、および5分間隔。2026-12-19 00:00 JSTで両トリガー失効。
- Interactive / Limited / Hidden / IgnoreNew。管理者権限・保存パスワードは使わない。
- Windows再起動後は **ユーザーのログオンが必要**。電源断・スリープ中の収集やログオン前の復旧は保証しない。
- 健全なcollectorのleaseがある場合、短いread-only SELECTだけで終了。毎回のモデル・履歴全走査はしない。

## 起動が必要な場合の確認

既存collectorが不在、明示停止なし、試験期間内であることを確認する。生きたプロセスがleaseなしで残る場合は手動調査とし、自動killや重複起動はしない。

起動直前にはprotocol、全35凍結ソース、4成果物、分析定義、評価ソース、既存launcher、当日の履歴bindingのhashを再検査する。変更があれば起動しない。08:00以降に当日の履歴準備がない場合も、日中に重い準備を始めず停止する。

新規起動は既存 `start-forward-collector.ps1` を非表示で呼び出す。既存のlease/fenceと停止フラグを引き続き使用し、`--resume-stopped` は渡さない。途中で結果不明になったraceの再取得は既存ルールどおり行わない。

## 操作と監査

読み取り確認: `powershell.exe -NoProfile -File scripts/ensure-forward-collector-resilience.ps1`。全hash監査は `-Audit` を付ける。これらはcollectorを起動しない。

停止は既存の `scripts/run_forward_collector.py stop --root reports/generated/forward-runtime/h04-live-20260920-v2` を使う。起動補助は停止フラグを自動解除しない。

外部guardログは `reports/generated/forward-runtime-ops/h04-live-20260920-v2/`。固定済みの収集プログラムと分離して保存する。Taskのwrapper SHAと、wrapper内guard SHAを別途固定しているため、補助スクリプトの変更時も明示的な点検とTask再登録が必要。

初回の実スケジュール実行は2026-09-20 00:20:51 JST、終了値0、`already_running`・新規起動なし。実際の再起動・稼働中collectorの停止は行っていないので、reboot後の起動経路を実機実証したとは扱わない。

履歴は2026-09-13まで（当日比7日遅れ）。また既存の90日カレンダーには29開催日、レース上限828件しかなく、固定された30日/1000件の到達条件を満たせない。条件変更や期間延長はこの対策に含めない。
