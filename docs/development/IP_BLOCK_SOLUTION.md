# netkeiba.comスクレイピング - IPブロック問題の診断と解決策

## 📋 現状の診断結果

### 実行した診断テスト（2026年1月11日）

| テスト方法 | 結果 | 詳細 |
|----------|------|------|
| **Python requests** | ❌ 400 Bad Request | 全URLで400エラー、Content-Length: 0 bytes |
| **Selenium + Edge** | ❌ 起動失敗 | サービスが即座に終了 |
| **Playwright + stealth** | ❌ 400 / HTTP_RESPONSE_CODE_FAILURE | headless/非headless両方で失敗 |
| **undetected-chromedriver** | ❌ セッション切断 | ブラウザが即座に閉じる |
| **無料プロキシ** | ❌ 接続失敗 | 全プロキシが利用不可 |

### 結論
**現在のIPアドレス `180.46.30.140` がnetkeiba.comにブロックされています**

---

## 🚨 ブロックの原因

1. **過度なリクエスト**: 短時間に多数のテストリクエストを送信
2. **bot検出**: User-Agentやリクエストパターンの検出
3. **IPレベルのブロック**: Apache/WAFレベルでの遮断

netkeiba.comは非常に厳しいスクレイピング対策を実施しています。

---

## ✅ 解決策（優先順位順）

### 【即座に試せる無料の方法】

#### 1. ⏰ 時間を置く（最も簡単）
- **待機時間**: 6〜24時間
- **理由**: レート制限が時間経過で解除される可能性
- **次回実行時の注意**:
  ```python
  import time
  import random
  
  # 各リクエスト間に3〜7秒待機
  time.sleep(random.uniform(3, 7))
  ```

#### 2. 📱 スマホのテザリングを使用
- **手順**:
  1. スマホのテザリング機能をON
  2. PCをテザリングに接続
  3. スクリプトを実行
- **メリット**: モバイルネットワークは別のIPアドレス
- **コスト**: 無料（データ通信量のみ）

#### 3. 🔐 VPN（無料版）
- **ProtonVPN** (無料): https://protonvpn.com/
  - 無料プランあり
  - 日本サーバーあり
  - 手順:
    1. ProtonVPNをインストール
    2. 日本または他国のサーバーに接続
    3. `python test_actual_working_raceid.py` を実行

---

### 【有料だが確実な方法】

#### 4. 💰 有料プロキシサービス（推奨）

##### **SmartProxy**（最もコスパが良い）
- **URL**: https://smartproxy.com/
- **価格**: $75/月〜
- **特徴**: 住宅用プロキシ、日本IP利用可能
- **実装例**:
```python
import requests

proxies = {
    'http': 'http://username:password@gate.smartproxy.com:7000',
    'https': 'http://username:password@gate.smartproxy.com:7000'
}

response = requests.get(
    'https://race.netkeiba.com/race/shutuba.html?race_id=202606010411',
    proxies=proxies,
    headers={'User-Agent': 'Mozilla/5.0...'}
)
```

##### **Bright Data**（最高品質）
- **URL**: https://brightdata.com/
- **価格**: $500/月〜
- **特徴**: 業界最高品質、住宅用IP、日本IP多数

##### **Oxylabs**
- **URL**: https://oxylabs.io/
- **価格**: $300/月〜

##### **ProxyMesh**（最安）
- **URL**: https://proxymesh.com/
- **価格**: $10/月〜（データセンターIP）

#### 5. 🌐 有料VPN
- **NordVPN**: https://nordvpn.com/ ($4/月〜)
- **ExpressVPN**: https://www.expressvpn.com/ ($7/月〜)

---

### 【長期的な解決策】

#### 6. ☁️ クラウドサーバー
異なるIPアドレスのサーバーから実行:
- **AWS EC2**: 東京リージョン（t2.micro $10/月）
- **GCP Compute Engine**: 東京ゾーン
- **Azure Virtual Machines**: 日本東部

#### 7. 🔄 プロキシローテーション
複数のプロキシを順番に使用:
```python
from itertools import cycle

proxy_pool = [proxy1, proxy2, proxy3, ...]
proxy_cycle = cycle(proxy_pool)

def scrape_with_rotation(url):
    proxy = next(proxy_cycle)
    return requests.get(url, proxies=proxy)
```

---

## 📝 今後の実装で守るべきルール

### レート制限の遵守
```python
import time
import random

def safe_scrape(url):
    # 各リクエスト前に待機
    time.sleep(random.uniform(3, 7))
    
    response = requests.get(url, headers=headers)
    
    # 429エラーの場合は長めに待機
    if response.status_code == 429:
        time.sleep(60)
        return safe_scrape(url)  # リトライ
    
    return response
```

### エラーハンドリング
```python
def robust_scrape(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:
                wait_time = (2 ** attempt) * 60  # Exponential backoff
                time.sleep(wait_time)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(30)
    return None
```

---

## 🎯 次のステップ（推奨順）

### 今すぐ実行
1. ✅ **6〜24時間待つ** → 無料、最も簡単
2. ✅ **スマホのテザリングで試す** → 無料、即効性あり
3. ✅ **ProtonVPN無料版を試す** → 無料

### 本格運用の準備
4. **有料プロキシの契約**
   - SmartProxy ($75/月) を推奨
   - または ProxyMesh ($10/月) で試験運用

5. **スクリプトの改善**
   - レート制限の実装
   - エラーハンドリングの追加
   - プロキシローテーション

---

## 📞 サポート情報

### 作成したテストスクリプト
- `test_with_proxy.py` - プロキシテスト
- `ip_block_solutions.py` - 解決策ガイド
- `test_actual_working_raceid.py` - 基本接続テスト

### VPN接続後の確認コマンド
```bash
# IPアドレス確認
python -c "import requests; print(requests.get('https://api.ipify.org').text)"

# netkeiba.com接続テスト
python test_actual_working_raceid.py
```
2026-05-07 14:47:22,348 - WARNING - 20200626: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 14:47:22,348 - WARNING - 連続5回のIPブロック検知 → 120秒待機
INFO:     10.132.114.7:59528 - "GET / HTTP/1.1" 200 OK
INFO:     ('10.132.114.7', 59538) - "WebSocket /" 403
INFO:     connection rejected (403 Forbidden)
INFO:     connection closed
INFO:     10.132.114.7:59539 - "GET /loginMsg.js HTTP/1.1" 404 Not Found
INFO:     10.132.114.7:59540 - "GET /cgi/get.cgi?cmd=home_login HTTP/1.1" 404 Not Found
INFO:     10.132.114.7:59542 - "POST /boaform/formTracert HTTP/1.1" 404 Not Found
2026-05-07 14:49:22,348 - INFO - 20200626: 0レースID検出
2026-05-07 14:49:22,348 - INFO - 20200626: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:49:35,545 - WARNING - 20200627: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 14:49:35,545 - INFO - 20200627: 0レースID検出
2026-05-07 14:49:35,546 - INFO - 20200627: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:49:48,627 - WARNING - 20200628: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 14:49:48,628 - INFO - 20200628: 0レースID検出
2026-05-07 14:49:48,628 - INFO - 20200628: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:50:01,669 - WARNING - 20200629: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 14:50:01,670 - INFO - 20200629: 0レースID検出
2026-05-07 14:50:01,670 - INFO - 20200629: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:50:14,804 - WARNING - 20200630: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 14:50:14,804 - INFO - 20200630: 0レースID検出
2026-05-07 14:50:14,804 - INFO - 20200630: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:50:27,961 - WARNING - 20200701: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 14:50:27,961 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 14:52:27,963 - INFO - 20200701: 0レースID検出
2026-05-07 14:52:27,963 - INFO - 20200701: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:52:41,179 - WARNING - 20200702: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 14:52:41,179 - INFO - 20200702: 0レースID検出
2026-05-07 14:52:41,180 - INFO - 20200702: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:52:54,262 - WARNING - 20200703: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 14:52:54,263 - INFO - 20200703: 0レースID検出
2026-05-07 14:52:54,263 - INFO - 20200703: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:53:07,344 - WARNING - 20200704: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 14:53:07,344 - INFO - 20200704: 0レースID検出
2026-05-07 14:53:07,344 - INFO - 20200704: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:53:20,419 - WARNING - 20200705: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 14:53:20,419 - INFO - 20200705: 0レースID検出
2026-05-07 14:53:20,419 - INFO - 20200705: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:53:33,497 - WARNING - 20200706: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 14:53:33,497 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 14:55:33,511 - INFO - 20200706: 0レースID検出
2026-05-07 14:55:33,511 - INFO - 20200706: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:55:46,706 - WARNING - 20200707: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 14:55:46,706 - INFO - 20200707: 0レースID検出
2026-05-07 14:55:46,706 - INFO - 20200707: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:55:59,787 - WARNING - 20200708: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 14:55:59,787 - INFO - 20200708: 0レースID検出
2026-05-07 14:55:59,787 - INFO - 20200708: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:56:12,892 - WARNING - 20200709: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 14:56:12,893 - INFO - 20200709: 0レースID検出
2026-05-07 14:56:12,893 - INFO - 20200709: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:56:25,976 - WARNING - 20200710: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 14:56:25,976 - INFO - 20200710: 0レースID検出
2026-05-07 14:56:25,977 - INFO - 20200710: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:56:39,059 - WARNING - 20200711: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 14:56:39,059 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 14:58:39,060 - INFO - 20200711: 0レースID検出
2026-05-07 14:58:39,060 - INFO - 20200711: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:58:52,256 - WARNING - 20200712: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 14:58:52,257 - INFO - 20200712: 0レースID検出
2026-05-07 14:58:52,257 - INFO - 20200712: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:59:05,384 - WARNING - 20200713: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 14:59:05,384 - INFO - 20200713: 0レースID検出
2026-05-07 14:59:05,385 - INFO - 20200713: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:59:18,452 - WARNING - 20200714: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 14:59:18,452 - INFO - 20200714: 0レースID検出
2026-05-07 14:59:18,452 - INFO - 20200714: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:59:31,578 - WARNING - 20200715: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 14:59:31,578 - INFO - 20200715: 0レースID検出
2026-05-07 14:59:31,578 - INFO - 20200715: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 14:59:44,649 - WARNING - 20200716: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 14:59:44,649 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:00:00,252 - DEBUG - Looking for jobs to run
2026-05-07 15:00:00,252 - DEBUG - Next wakeup is due at 2026-05-07 17:00:00+09:00 (in 7199.747788 seconds)
2026-05-07 15:00:00,252 - INFO - Running job "_job_scrape_today (trigger: cron[hour='9,11,13,15,17,19,21', minute='0'], next run at: 2026-05-07 17:00:00 JST)" (scheduled at 2026-05-07 15:00:00+09:00)
2026-05-07 15:00:00,253 - INFO - [scheduler] 当日スクレイプ: 20260507
2026-05-07 15:00:00,515 - DEBUG - connect_tcp.started host='race.netkeiba.com' port=443 local_address=None timeout=20.0 socket_options=None
2026-05-07 15:00:00,559 - DEBUG - connect_tcp.complete return_value=<httpcore._backends.anyio.AnyIOStream object at 0x00000239A8D88650>
2026-05-07 15:00:00,559 - DEBUG - start_tls.started ssl_context=<ssl.SSLContext object at 0x00000239A64322A0> server_hostname='race.netkeiba.com' timeout=20.0
2026-05-07 15:00:00,602 - DEBUG - start_tls.complete return_value=<httpcore._backends.anyio.AnyIOStream object at 0x00000239A7A0D7D0>
2026-05-07 15:00:00,602 - DEBUG - send_request_headers.started request=<Request [b'GET']>
2026-05-07 15:00:00,602 - DEBUG - send_request_headers.complete
2026-05-07 15:00:00,603 - DEBUG - send_request_body.started request=<Request [b'GET']>
2026-05-07 15:00:00,603 - DEBUG - send_request_body.complete
2026-05-07 15:00:00,603 - DEBUG - receive_response_headers.started request=<Request [b'GET']>
2026-05-07 15:00:00,681 - DEBUG - receive_response_headers.complete return_value=(b'HTTP/1.1', 400, b'Bad Request', [(b'Content-Type', b'text/html; charset='), (b'Content-Length', b'0'), (b'Server', b'Apache'), (b'Access-Control-Allow-Credentials', b'true'), (b'Date', b'Thu, 07 May 2026 06:00:00 GMT'), (b'Connection', b'keep-alive')])
2026-05-07 15:00:00,681 - INFO - HTTP Request: GET https://race.netkeiba.com/top/race_list.html?kaisai_date=20260507 "HTTP/1.1 400 Bad Request"
2026-05-07 15:00:00,681 - DEBUG - receive_response_body.started request=<Request [b'GET']>
2026-05-07 15:00:00,682 - DEBUG - receive_response_body.complete
2026-05-07 15:00:00,682 - DEBUG - response_closed.started
2026-05-07 15:00:00,682 - DEBUG - response_closed.complete
2026-05-07 15:00:00,683 - DEBUG - close.started
2026-05-07 15:00:00,683 - DEBUG - close.complete
2026-05-07 15:00:00,683 - WARNING - レース一覧取得失敗 20260507: Client error '400 Bad Request' for url 'https://race.netkeiba.com/top/race_list.html?kaisai_date=20260507'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400
2026-05-07 15:00:00,683 - INFO - [scheduler] 20260507: 0 races scraped
2026-05-07 15:00:00,684 - INFO - Job "_job_scrape_today (trigger: cron[hour='9,11,13,15,17,19,21', minute='0'], next run at: 2026-05-07 17:00:00 JST)" executed successfully
2026-05-07 15:01:44,658 - INFO - 20200716: 0レースID検出
2026-05-07 15:01:44,658 - INFO - 20200716: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:01:57,853 - WARNING - 20200717: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:01:57,853 - INFO - 20200717: 0レースID検出
2026-05-07 15:01:57,853 - INFO - 20200717: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:02:10,911 - WARNING - 20200718: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:02:10,911 - INFO - 20200718: 0レースID検出
2026-05-07 15:02:10,911 - INFO - 20200718: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:02:23,968 - WARNING - 20200719: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:02:23,968 - INFO - 20200719: 0レースID検出
2026-05-07 15:02:23,968 - INFO - 20200719: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:02:37,049 - WARNING - 20200720: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:02:37,049 - INFO - 20200720: 0レースID検出
2026-05-07 15:02:37,049 - INFO - 20200720: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:02:50,124 - WARNING - 20200721: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 15:02:50,124 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:04:50,143 - INFO - 20200721: 0レースID検出
2026-05-07 15:04:50,143 - INFO - 20200721: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:05:03,341 - WARNING - 20200722: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:05:03,341 - INFO - 20200722: 0レースID検出
2026-05-07 15:05:03,341 - INFO - 20200722: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:05:16,418 - WARNING - 20200723: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:05:16,418 - INFO - 20200723: 0レースID検出
2026-05-07 15:05:16,418 - INFO - 20200723: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:05:29,499 - WARNING - 20200724: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:05:29,499 - INFO - 20200724: 0レースID検出
2026-05-07 15:05:29,499 - INFO - 20200724: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:05:42,599 - WARNING - 20200725: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:05:42,599 - INFO - 20200725: 0レースID検出
2026-05-07 15:05:42,599 - INFO - 20200725: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:05:55,663 - WARNING - 20200726: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 15:05:55,663 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:07:55,688 - INFO - 20200726: 0レースID検出
2026-05-07 15:07:55,688 - INFO - 20200726: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:08:08,886 - WARNING - 20200727: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:08:08,886 - INFO - 20200727: 0レースID検出
2026-05-07 15:08:08,886 - INFO - 20200727: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:08:21,958 - WARNING - 20200728: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:08:21,958 - INFO - 20200728: 0レースID検出
2026-05-07 15:08:21,958 - INFO - 20200728: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:08:35,044 - WARNING - 20200729: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:08:35,044 - INFO - 20200729: 0レースID検出
2026-05-07 15:08:35,044 - INFO - 20200729: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:08:48,099 - WARNING - 20200730: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:08:48,099 - INFO - 20200730: 0レースID検出
2026-05-07 15:08:48,099 - INFO - 20200730: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:09:01,173 - WARNING - 20200731: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 15:09:01,173 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:11:01,189 - INFO - 20200731: 0レースID検出
2026-05-07 15:11:01,189 - INFO - 20200731: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:11:14,509 - WARNING - 20200801: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:11:14,509 - INFO - 20200801: 0レースID検出
2026-05-07 15:11:14,509 - INFO - 20200801: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:11:27,560 - WARNING - 20200802: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:11:27,560 - INFO - 20200802: 0レースID検出
2026-05-07 15:11:27,560 - INFO - 20200802: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:11:40,629 - WARNING - 20200803: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:11:40,629 - INFO - 20200803: 0レースID検出
2026-05-07 15:11:40,629 - INFO - 20200803: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:11:53,718 - WARNING - 20200804: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:11:53,718 - INFO - 20200804: 0レースID検出
2026-05-07 15:11:53,718 - INFO - 20200804: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:12:06,766 - WARNING - 20200805: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 15:12:06,766 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:14:06,775 - INFO - 20200805: 0レースID検出
2026-05-07 15:14:06,775 - INFO - 20200805: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:14:19,963 - WARNING - 20200806: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:14:19,963 - INFO - 20200806: 0レースID検出
2026-05-07 15:14:19,963 - INFO - 20200806: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:14:33,043 - WARNING - 20200807: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:14:33,043 - INFO - 20200807: 0レースID検出
2026-05-07 15:14:33,043 - INFO - 20200807: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:14:46,107 - WARNING - 20200808: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:14:46,107 - INFO - 20200808: 0レースID検出
2026-05-07 15:14:46,108 - INFO - 20200808: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:14:59,212 - WARNING - 20200809: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:14:59,212 - INFO - 20200809: 0レースID検出
2026-05-07 15:14:59,212 - INFO - 20200809: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:15:12,289 - WARNING - 20200810: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続5回)
2026-05-07 15:15:12,289 - WARNING - 連続5回のIPブロック検知 → 120秒待機
2026-05-07 15:17:12,290 - INFO - 20200810: 0レースID検出
2026-05-07 15:17:12,290 - INFO - 20200810: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:17:25,484 - WARNING - 20200811: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続1回)
2026-05-07 15:17:25,484 - INFO - 20200811: 0レースID検出
2026-05-07 15:17:25,484 - INFO - 20200811: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:17:38,584 - WARNING - 20200812: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続2回)
2026-05-07 15:17:38,584 - INFO - 20200812: 0レースID検出
2026-05-07 15:17:38,584 - INFO - 20200812: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:17:51,630 - WARNING - 20200813: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続3回)
2026-05-07 15:17:51,630 - INFO - 20200813: 0レースID検出
2026-05-07 15:17:51,630 - INFO - 20200813: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
2026-05-07 15:18:04,688 - WARNING - 20200814: db.netkeiba.com HTTP 400 空レスポンス → IPブロックの可能性 (連続4回)
2026-05-07 15:18:04,688 - INFO - 20200814: 0レースID検出
2026-05-07 15:18:04,689 - INFO - 20200814: IPブロック疑い（0件取得済み）→ scraped_dates に記録しない（再スクレイプ可能）
---

## ⚠️ 重要な注意事項

1. **利用規約の確認**: netkeiba.comの利用規約を確認し、遵守してください
2. **適切な間隔**: 各リクエスト間は最低3秒以上空ける
3. **エラー対応**: 429エラーが出たら即座に停止
4. **個人情報**: プロキシ経由の場合、認証情報を安全に管理

---

**現在のIPアドレス**: `180.46.30.140` （ブロック中）

次回アクセス時は、上記の解決策のいずれかを実施してください。
