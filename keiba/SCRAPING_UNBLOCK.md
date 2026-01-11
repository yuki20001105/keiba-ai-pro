# IPブロック回避方法

Netkeibaからスクレイピングしすぎてブロックされた場合の対処法です。

## 🚨 現在の状況

24時間ブロックされている場合、以下の方法で回避できます：

---

## 方法1: ブラウザモード（Playwright）【推奨】

実際のブラウザを使ってアクセスするため、最もブロックされにくい方法です。

### セットアップ

```bash
# 1. Playwrightをインストール
pip install playwright

# 2. Chromiumブラウザをインストール
playwright install chromium
```

### 使い方

```bash
# テストスクリプトで動作確認
python test_playwright_scraping.py
```

#### Pythonコードで使用

```python
from keiba_ai.config import load_config
from keiba_ai.netkeiba.browser_client import PlaywrightClient
from pathlib import Path

cfg = load_config(Path("config.yaml"))

# ブラウザクライアントを作成
with PlaywrightClient(cfg.netkeiba, cfg.storage, headless=True) as client:
    # HTMLを取得
    result = client.fetch_html(
        url="https://race.netkeiba.com/race/shutuba.html?race_id=202406050811",
        cache_kind="shutuba",
        cache_key="202406050811",
        use_cache=False
    )
    print(f"取得成功: {len(result.text)} 文字")
```

### メリット

- ✅ **最もブロックされにくい** - 実際のブラウザを使用
- ✅ **JavaScriptが動作** - 動的コンテンツも取得可能
- ✅ **人間らしいアクセス** - ランダムな待機時間、スクロールなど
- ✅ **webdriver検出を回避** - 自動化検出を無効化

### デメリット

- ⚠️ 初回起動が遅い（ブラウザの立ち上げに数秒）
- ⚠️ メモリ使用量が多い

---

## 方法2: ブラウザモード（Selenium）

Playwrightが使えない場合の代替案です。

### セットアップ

```bash
# Seleniumとブラウザドライバーをインストール
pip install selenium

# ChromeDriverを手動でダウンロードしてPATHに追加
# https://chromedriver.chromium.org/downloads
```

### 使い方

```python
from keiba_ai.netkeiba.browser_client import SeleniumClient

with SeleniumClient(cfg.netkeiba, cfg.storage, headless=True) as client:
    result = client.fetch_html(...)
```

---

## 方法3: 長い待機時間 + 少ないリクエスト

`config.yaml`を編集してより保守的な設定にします：

```yaml
netkeiba:
  min_sleep_sec: 30.0   # 30秒待機
  max_sleep_sec: 60.0   # 最大60秒
  max_pages_per_run: 10  # 1回に10ページまで
```

---

## 方法4: プロキシサーバー経由

**注意**: 無料プロキシは不安定で遅いことが多いです。

```python
import requests

proxies = {
    'http': 'http://proxy-server:port',
    'https': 'http://proxy-server:port',
}

session = requests.Session()
session.proxies.update(proxies)
```

---

## 方法5: 24時間待つ【確実】

最も確実な方法は、24時間待ってから再開することです。

その間に：
- ✅ 既存のキャッシュデータで学習・予測を試す
- ✅ 特徴量エンジニアリングの改善
- ✅ モデルの精度向上

---

## 推奨フロー

1. **まず24時間待つ**
2. **Playwrightをインストール**して次回からブロックされにくくする
3. **config.yamlで待機時間を長く設定**（30-60秒）
4. **1日10-20レースずつ取得**する（焦らない）

---

## トラブルシューティング

### Playwrightのインストールエラー

```bash
# 管理者権限で実行
playwright install chromium --with-deps
```

### "Chromium executable doesn't exist"

```bash
# ブラウザを再インストール
playwright install --force chromium
```

### それでもブロックされる場合

- VPNを使用してIPアドレスを変更
- モバイルテザリング経由でアクセス
- 数日間スクレイピングを休む

---

## 参考情報

- Playwright公式: https://playwright.dev/python/
- Selenium公式: https://www.selenium.dev/
- Netkeiba利用規約を必ず確認してください
