# Playwright版サーバーの停止
# Usage: .\stop_playwright_server.ps1

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Stop Playwright Server" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# ポート8002を使用しているプロセスを検索
Write-Host "ポート8002を使用しているプロセスを検索中..." -ForegroundColor Yellow
$connection = Get-NetTCPConnection -LocalPort 8002 -ErrorAction SilentlyContinue

if ($connection) {
    $pid = $connection.OwningProcess | Select-Object -First 1
    $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
    
    if ($process) {
        Write-Host "  プロセス発見: $($process.ProcessName) (PID: $pid)" -ForegroundColor White
        Write-Host ""
        Write-Host "プロセスを停止しますか？ (Y/N): " -NoNewline -ForegroundColor Yellow
        $response = Read-Host
        
        if ($response -eq "Y" -or $response -eq "y") {
            Stop-Process -Id $pid -Force
            Start-Sleep -Seconds 2
            
            # 停止確認
            $stillRunning = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($stillRunning) {
                Write-Host "  ✗ プロセスの停止に失敗しました" -ForegroundColor Red
            } else {
                Write-Host "  ✓ サーバーを停止しました" -ForegroundColor Green
            }
        } else {
            Write-Host "  キャンセルしました" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  ✗ プロセス情報を取得できませんでした" -ForegroundColor Red
    }
} else {
    Write-Host "  ✓ サーバーは起動していません" -ForegroundColor Green
}

Write-Host ""
