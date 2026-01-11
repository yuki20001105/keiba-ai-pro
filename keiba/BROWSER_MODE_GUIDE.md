# 🚀 Playwrightブラウザモードの使い方

IPブロックを回避してデータを取得する方法です。

## 📦 セットアップ（初回のみ）

```powershell
# 1. Playwrightをインストール
pip install playwright

# 2. Chromiumブラウザをインストール
playwright install chromium
```

## ✅ 動作確認

```powershell
# テストスクリプトを実行
python test_playwright_scraping.py
```

レースIDが表示されれば成功です！

---

## 🎯 実際のデータ取得

### 方法1: テストスクリプトで1レース取得

```powershell
python test_browser_ingest.py
```

これで `202406050811`（2024年6月5日 東京8R）のデータが取得されます。

### 方法2: コマンドラインで任意のレース取得

```powershell
# --browser オプションを付けてブラウザモードを使用
python -m keiba_ai.ingest --browser race 202401010101
```

### 方法3: 日付指定で複数レース取得

```powershell
# 2024年1月1日のレース一覧を取得
python -m keiba_ai.ingest --browser date 20240101

# 出力されたrace_idを使って1件ずつ取得
python -m keiba_ai.ingest --browser race 202401010101
python -m keiba_ai.ingest --browser race 202401010102
# ...
```

---

## 💡 推奨ワークフロー

### 1日目: 少量テスト（5レース）

```powershell
# まずテスト
python test_browser_ingest.py

# 成功したら追加で数レース
python -m keiba_ai.ingest --browser race 202401010102
python -m keiba_ai.ingest --browser race 202401010103
python -m keiba_ai.ingest --browser race 202401010104
python -m keiba_ai.ingest --browser race 202401010105
```

**ポイント**: 各レース取得の間に10-20秒の待機時間があります。

### 2日目: 本格取得（10-20レース）

ブロックされていないことを確認してから、追加で取得します。

```powershell
# 別の日付のレースを取得
python -m keiba_ai.ingest --browser date 20240102
# ... レースIDを控える

# 1件ずつ取得（自動で10-20秒待機される）
python -m keiba_ai.ingest --browser race <レースID>
```

---

## ⚙️ config.yamlの設定

待機時間を調整できます：

```yaml
netkeiba:
  min_sleep_sec: 10.0  # 最小待機時間（秒）
  max_sleep_sec: 20.0  # 最大待機時間（秒）
  max_pages_per_run: 50  # 1回の実行で取得できる最大ページ数
```

**推奨設定**:
- `min_sleep_sec: 15.0`（より安全）
- `max_sleep_sec: 30.0`（より安全）
- `max_pages_per_run: 20`（控えめ）

---

## 🔍 取得データの確認

Streamlit UIで確認できます：

```powershell
streamlit run ui_app.py
```

1. **「4_DB確認」ページ**で取得したレースデータを確認
2. **「2_学習」ページ**で新しいモデルを学習
3. **「3_予測」ページ**で予測精度を確認

---

## ❗ トラブルシューティング

### エラー: "Playwright not installed"

```powershell
pip install playwright
playwright install chromium
```

### エラー: "Chromium executable doesn't exist"

```powershell
# 強制再インストール
playwright install --force chromium
```

### エラー: 400 Bad Request

まだブロックされている可能性があります：
1. 24時間待つ
2. VPNを使用する
3. モバイルテザリング経由でアクセス

### 取得が遅い

正常です！ブロック回避のため、各リクエストの間に10-20秒待機しています。

---

## 📊 取得の目安

| ページ数 | 待機時間合計 | 推奨頻度 |
|---------|------------|---------|
| 5ページ | 約1-2分 | 1日1回 |
| 10ページ | 約2-4分 | 1日1回 |
| 20ページ | 約5-7分 | 2-3日に1回 |
| 50ページ | 約10-17分 | 週1回 |

**重要**: 焦らず少しずつ取得しましょう！

---

## ✅ チェックリスト

- [ ] Playwrightをインストールした
- [ ] `playwright install chromium`を実行した
- [ ] `test_playwright_scraping.py`が成功した
- [ ] `test_browser_ingest.py`が成功した
- [ ] config.yamlの待機時間を確認した
- [ ] 1日の取得量を決めた（推奨: 5-20レース）
- [ ] 取得データをStreamlit UIで確認した

全てチェックできたら、安全にデータ取得を続けられます！
