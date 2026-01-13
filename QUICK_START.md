# 🚀 完全版アプリ 実行手順書

## 📋 クイックスタート（3ステップ）

### 準備：既存プロセスの停止

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## ステップ1: スクレイピングサービス起動（ポート8001）

### 手動起動（推奨）
別のPowerShellウィンドウを開いて：

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python scraping_service_complete.py
```

**期待される出力:**
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

### 自動起動
```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
Start-Process powershell -ArgumentList "-NoExit", "-Command", "python scraping_service_complete.py"
```

### 確認
```powershell
Invoke-RestMethod -Uri "http://localhost:8001/health"
# 期待: { "status": "ok", "version": "2.0.0-complete", "timestamp": "..." }
```

---

## ステップ2: Python API起動（ポート8000）

### 手動起動（推奨）
別のPowerShellウィンドウを開いて：

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:PYTHONPATH = "C:\Users\yuki2\Documents\ws\keiba-ai-pro"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**期待される出力:**
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
FastAPI起動 - ログ記録開始
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### エラーが出た場合
もし `ModuleNotFoundError` が出たら：

```powershell
# モジュールが見つからない場合
python -m pip install fastapi uvicorn pydantic

# インポートエラーの場合
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python -c "import sys; sys.path.insert(0, 'keiba'); from keiba_ai.config import load_config; print('OK')"
```

### 確認
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/"
# 期待: { "status": "ok", "service": "Keiba AI - Machine Learning API", "version": "1.0.0" }
```

---

## ステップ3: Next.js起動（ポート3000）

### 手動起動（推奨）
別のPowerShellウィンドウを開いて：

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
npm run dev
```

**期待される出力:**
```
- ready started server on 0.0.0.0:3000, url: http://localhost:3000
- event compiled client and server successfully
```

### 確認
ブラウザで http://localhost:3000 を開く

---

## 🧪 統合テスト実行

全サービスが起動したら：

```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python test_integration.py
```

**期待される結果:**
```
================================================================================
  テスト結果サマリー
================================================================================

  合計テスト: 13
  成功: 11-13
  失敗: 0-2
  スキップ: 0
```

---

## 📝 使用例

### 1. race_id取得テスト

```powershell
$body = @{kaisai_date="20240108"} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8001/api/race_list" -Method POST -Body $body -ContentType "application/json"
```

### 2. AI予測モデル学習（Standard版）

```powershell
$trainBody = @{
    target = "win"
    model_type = "logistic_regression"
    test_size = 0.2
    cv_folds = 5
    use_sqlite = $true
    ultimate_mode = $false
    use_optuna = $false
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/train" -Method POST -Body $trainBody -ContentType "application/json"
```

### 3. AI予測モデル学習（Ultimate版 + Optuna最適化）

```powershell
$ultimateBody = @{
    target = "win"
    model_type = "random_forest"
    test_size = 0.2
    cv_folds = 5
    use_sqlite = $true
    ultimate_mode = $true    # Ultimate特徴量ON
    use_optuna = $true       # Optuna最適化ON
    optuna_trials = 50
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/train" -Method POST -Body $ultimateBody -ContentType "application/json"
```

---

## 🔧 トラブルシューティング

### Python APIが起動しない

**症状:** `ModuleNotFoundError: No module named 'keiba_ai'`

**解決策:**
```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro\python-api
$env:PYTHONPATH = "C:\Users\yuki2\Documents\ws\keiba-ai-pro"
python -c "import sys; print(sys.path)"
```

### スクレイピングでrace_idが0件

**症状:** race_idが取得できない

**解決策:**
1. 実在する開催日を指定（例: 20240106, 20240107など）
2. netkeibaのサイトで開催日を確認
3. VPN使用時は日本のIPアドレスに変更

### データベースが見つからない

**症状:** `no such table: races`

**解決策:**
```powershell
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro
python -c "import sys; sys.path.insert(0, 'keiba'); from keiba_ai.db import connect, init_db; conn = connect(); init_db(conn); print('DB初期化完了')"
```

---

## 📊 各サービスの役割

| サービス | ポート | 役割 |
|---------|--------|------|
| スクレイピング | 8001 | netkeibaからrace_id取得 |
| Python API | 8000 | 機械学習モデルの学習・予測 |
| Next.js | 3000 | フロントエンドUI |

---

## 🎯 実装済み機能

### ✅ スクレイピング（完全修正）
- 動的API対応（race_list_get_date_list.html）
- 5パターンの正規表現でrace_id抽出
- フォールバック機構
- レート制限（2-3秒ランダム）

### ✅ Ultimate版特徴量（完全実装）
- 過去10走統計（13特徴量）
- 騎手統計（5特徴量）
- 調教師統計（4特徴量）
- 合計22特徴量を自動計算

### ✅ 全モデルOptuna最適化
- Logistic Regression
- Random Forest
- Gradient Boosting
- LightGBM（既存）

---

## 🚦 起動完了の確認

すべてのサービスが起動していることを確認：

```powershell
# 1つのコマンドで全サービス確認
@(
    @{Name="スクレイピング"; Url="http://localhost:8001/health"},
    @{Name="Python API"; Url="http://localhost:8000/"},
    @{Name="Next.js"; Url="http://localhost:3000"}
) | ForEach-Object {
    Write-Host "`n$($_.Name):" -ForegroundColor Yellow
    try {
        $r = Invoke-RestMethod -Uri $_.Url -TimeoutSec 3 -ErrorAction Stop
        Write-Host "  ✓ 起動OK" -ForegroundColor Green
    } catch {
        Write-Host "  ✗ 起動失敗" -ForegroundColor Red
    }
}
```

すべて「✓ 起動OK」なら準備完了です！🎉
