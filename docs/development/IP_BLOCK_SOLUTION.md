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

---

## ⚠️ 重要な注意事項

1. **利用規約の確認**: netkeiba.comの利用規約を確認し、遵守してください
2. **適切な間隔**: 各リクエスト間は最低3秒以上空ける
3. **エラー対応**: 429エラーが出たら即座に停止
4. **個人情報**: プロキシ経由の場合、認証情報を安全に管理

---

**現在のIPアドレス**: `180.46.30.140` （ブロック中）

次回アクセス時は、上記の解決策のいずれかを実施してください。
