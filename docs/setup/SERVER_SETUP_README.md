# Playwright版サーバー管理スクリプト - README

## 概要
Playwright版スクレイピングサーバーの起動・停止・確認を行うPowerShellスクリプト集

## 使用方法

### 1. サーバー起動
```powershell
.\start_playwright_server.ps1
```

**機能:**
- ポート8002の使用状況を確認
- Python環境をチェック
- 必要なファイルの存在確認
- サーバーを起動

**表示される情報:**
- URL: http://localhost:8002
- エンジン: Playwright
- 並列数: 3 (ページプール)
- 終了方法: Ctrl+C

### 2. サーバー確認
```powershell
.\check_server.ps1
```

**機能:**
- ポート8002の使用状況確認
- ヘルスチェック (GET /health)
- キャッシュ状況の表示
- 利用可能なエンドポイント一覧

### 3. サーバー停止
```powershell
.\stop_playwright_server.ps1
```

**機能:**
- ポート8002を使用しているプロセスを検索
- プロセス情報を表示
- 確認後にプロセスを停止

## エンドポイント

### GET /health
サーバーの状態確認
```json
{
  "status": "ok",
  "engine": "playwright",
  "jockey_cache_size": 0,
  "trainer_cache_size": 0
}
```

### POST /cache/clear
キャッシュをクリア
```json
{
  "message": "Cache cleared"
}
```

### POST /scrape/ultimate
1レースをスクレイピング
```json
{
  "race_id": "202406010101",
  "include_details": true
}
```

### POST /scrape/ultimate/batch_by_period
期間指定でバッチスクレイピング
```json
{
  "start_date": "20240601",
  "end_date": "20240601",
  "include_details": true,
  "max_workers": 7
}
```

## システム要件

- Windows 10/11
- Python 3.10+
- PowerShell 5.1+
- Playwright (chromium)

## トラブルシューティング

### ポート8002が既に使用されている
```powershell
# ポート使用中のプロセスを確認
Get-NetTCPConnection -LocalPort 8002

# プロセスを停止
.\stop_playwright_server.ps1
```

### サーバーが応答しない
```powershell
# ヘルスチェック
.\check_server.ps1

# サーバーログを確認（サーバー起動ウィンドウ）
```

### Playwrightのインストール
```powershell
pip install playwright
playwright install chromium
```

## 性能特性

### ページプール方式
- 固定3ページを使い回し
- Chromeウィンドウの開きすぎを防止
- メモリ使用量を安定化

### 並列処理
- セマフォで3並列に制限
- 詳細ページ取得時のみ並列化
- タイムアウト: 60秒

### キャッシュ
- 騎手詳細: メモリキャッシュ
- 調教師詳細: メモリキャッシュ
- `/cache/clear` で手動クリア可能

## Phase 2テスト結果

### 高速モード
- 実行時間: 約6秒
- 特徴量: 27項目
- カバレッジ: 100%

### 完全モード
- 実行時間: 約187秒
- 馬詳細: 16/16頭
- 騎手詳細: 16/16頭
- 調教師詳細: 16/16頭

## 関連ファイル

- `scraping_service_playwright.py` - メインサーバー
- `test_playwright_phase2.py` - Phase 2テスト
- `test_playwright_phase3.py` - Phase 3テスト（バッチ処理）
