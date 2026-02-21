# Playwright版サーバーの起動確認とヘルスチェック
# Usage: .\check_server.ps1

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Server Health Check" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# ポート8002の確認
Write-Host "[1/2] ポート8002の使用状況..." -ForegroundColor Yellow
$connection = Get-NetTCPConnection -LocalPort 8002 -ErrorAction SilentlyContinue
if ($connection) {
    $pid = $connection.OwningProcess | Select-Object -First 1
    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
    Write-Host "  ✓ サーバーが起動しています (PID: $pid)" -ForegroundColor Green
    if ($process) {
        Write-Host "    プロセス: $($process.ProcessName)" -ForegroundColor White
        Write-Host "    起動時刻: $($process.StartTime)" -ForegroundColor White
    }
} else {
    Write-Host "  ✗ サーバーが起動していません" -ForegroundColor Red
    Write-Host ""
    Write-Host "サーバーを起動するには:" -ForegroundColor Yellow
    Write-Host "  .\start_playwright_server.ps1" -ForegroundColor White
    exit
}

# ヘルスチェック
Write-Host ""
Write-Host "[2/2] ヘルスチェック..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8002/health" -Method Get -TimeoutSec 5
    Write-Host "  ✓ サーバーは正常に応答しています" -ForegroundColor Green
    Write-Host ""
    Write-Host "サーバー情報:" -ForegroundColor Cyan
    Write-Host "  Status: $($response.status)" -ForegroundColor White
    Write-Host "  Engine: $($response.engine)" -ForegroundColor White
    Write-Host "  Jockey Cache: $($response.jockey_cache_size)" -ForegroundColor White
    Write-Host "  Trainer Cache: $($response.trainer_cache_size)" -ForegroundColor White
} catch {
    Write-Host "  ✗ サーバーが応答しません" -ForegroundColor Red
    Write-Host "  エラー: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "利用可能なエンドポイント" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  GET  /health" -ForegroundColor White
Write-Host "  POST /cache/clear" -ForegroundColor White
Write-Host "  POST /scrape/ultimate" -ForegroundColor White
Write-Host "  POST /scrape/ultimate/batch_by_period" -ForegroundColor White
Write-Host ""
