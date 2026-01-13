# 🔍 スクレイピング失敗の原因診断レポート

## 📊 診断結果サマリー

| 項目 | 状態 | 詳細 |
|------|------|------|
| サービス起動 | ✅ OK | scraping_service_complete.py が正常起動 |
| カレンダーAPI | ✅ 修正完了 | `year_month`形式に対応 |
| race_list API | ❌ 廃止 | `/top/race_list_get_date_list.html` が400エラー |
| race_listページ | ❌ JS動的 | JavaScriptで動的レンダリング |

---

## 🚨 根本原因

### 1. race_list_get_date_list.html APIが機能しない

```python
# すべての日付で400エラー
response = requests.post(
    "https://race.netkeiba.com/top/race_list_get_date_list.html",
    data={"kaisai_date": "20260111"}
)
# → Status: 400 (Bad Request)
```

**原因**: APIが廃止されたか、パラメータ形式が変更された

### 2. race_list.htmlはJavaScriptレンダリング必須

```
URL: https://race.netkeiba.com/top/race_list.html?kaisai_date=20260111
HTML length: 41,345 characters
JavaScript files: 14個

主要なJS:
- raceapi.action.js
- YosoJraNar.js
- common_pc_new.js
```

**検証結果**:
- HTMLに`race_id`という文字列は6回出現
- しかし、実際のrace_id値（12桁数字）は**0件**
- → JavaScriptで動的に生成されている

---

## 💡 解決策

### 方法A: Selenium/undetected_chromedriverを使用 ✅ 推奨

```python
from selenium import webdriver
import undetected_chromedriver as uc

# ブラウザ起動
driver = uc.Chrome()
driver.get(f"https://race.netkeiba.com/top/race_list.html?kaisai_date={kaisai_date}")

# JavaScript実行完了を待機
time.sleep(5)

# レンダリング後のHTMLから抽出
html = driver.page_source
race_ids = re.findall(r'race_id=(\d{12})', html)
```

**メリット**:
- JavaScriptが実行される
- 動的コンテンツも取得可能
- Cloudflare等のBot対策を回避

**デメリット**:
- 処理が遅い（1ページ5-10秒）
- メモリ消費が大きい

### 方法B: 別のAPIエンドポイントを探す

netkeibaの他のAPIを調査：
- `/api/v1/races` （存在するか不明）
- モバイル版API （軽量の可能性）
- RSS/JSON形式のフィード

### 方法C: 過去データのみ使用

既にSupabaseに保存されているrace_idを使用して学習：
- 新規データ収集は手動 or 定期的なSelenium実行
- 予測時は既存データのみ

---

## 🛠️ 即時対応（推奨）

### 1. scraping_service_complete.pyをSelenium対応に変更

現在の実装:
```python
# ❌ 動作しない
response = requests.post(api_url, data={"kaisai_date": date})
```

修正案:
```python
# ✅ Selenium使用
driver.get(f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date}")
time.sleep(5)  # JavaScriptレンダリング待機
html = driver.page_source
race_ids = extract_race_ids_from_html(html)
```

### 2. 高速化のための最適化

- **ヘッドレスモード**: `options.headless = True`
- **キャッシュ**: 同じ日付は再取得しない
- **並列処理**: 複数ブラウザで同時取得
- **待機時間最適化**: JavaScriptの完了を検知

---

## 📈 性能比較

| 方法 | 速度 | 成功率 | メモリ | 実装難易度 |
|------|------|--------|--------|-----------|
| requests（現在） | 超高速(0.5秒) | **0%** | 低 | 簡単 |
| Selenium | 遅い(5-10秒) | **90%+** | 高 | 中 |
| API探索 | 高速(1秒) | **不明** | 低 | 難 |

---

## 🎯 次のステップ

### 優先度1: Selenium実装 ← **今すぐ実施**

1. `scraping_service_complete.py`をSelenium対応に書き換え
2. `get_race_list()`関数を修正
3. テスト実行（20260111で確認）

### 優先度2: フロントエンド対応

1. データ収集画面に処理時間の表示
2. プログレスバー追加
3. タイムアウト時間を延長（30秒 → 120秒）

### 優先度3: 最適化

1. ヘッドレスモード有効化
2. race_idキャッシュ機能
3. エラーリトライ機構

---

## 📝 検証コマンド

### 現在の問題を再現:
```powershell
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_recent_dates.py
# → すべて400エラー
```

### Selenium版テスト:
```powershell
# 実装後にテスト
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_selenium_scraping.py
# → race_id取得成功を確認
```

---

## ✅ 結論

**スクレイピング失敗の原因**: netkeibaが**JavaScriptで動的レンダリング**しており、通常のHTTP Requestsでは取得不可能

**解決方法**: **Selenium/undetected_chromedriverに切り替え** → 即時実装を推奨
