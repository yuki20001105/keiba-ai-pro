# Playwright版スクレイピングサーバー起動スクリプト
# Usage: .\start_playwright_server.ps1

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Playwright Scraping Server" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# ポート8002の使用状況を確認
Write-Host "[1/4] ポート8002の使用状況を確認中..." -ForegroundColor Yellow
$existingProcess = Get-NetTCPConnection -LocalPort 8002 -ErrorAction SilentlyContinue
if ($existingProcess) {
    Write-Host "  ⚠ ポート8002は既に使用されています" -ForegroundColor Red
    Write-Host "  既存のプロセスを停止しますか？ (Y/N): " -NoNewline -ForegroundColor Yellow
    $response = Read-Host
    if ($response -eq "Y" -or $response -eq "y") {
        $pid = $existingProcess.OwningProcess | Select-Object -First 1
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Write-Host "  ✓ プロセス停止完了" -ForegroundColor Green
        Start-Sleep -Seconds 2
    } else {
        Write-Host "  中止しました" -ForegroundColor Red
        exit
    }
} else {
    Write-Host "  ✓ ポート8002は使用可能です" -ForegroundColor Green
}

# Python環境の確認
Write-Host ""
Write-Host "[2/4] Python環境を確認中..." -ForegroundColor Yellow
$pythonVersion = python --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ $pythonVersion" -ForegroundColor Green
} else {
    Write-Host "  ✗ Pythonが見つかりません" -ForegroundColor Red
    exit
}

# 必要なファイルの確認
Write-Host ""
Write-Host "[3/4] 必要なファイルを確認中..." -ForegroundColor Yellow
$requiredFiles = @(
    "scraping_service_playwright.py"
)

$allFilesExist = $true
foreach ($file in $requiredFiles) {
    if (Test-Path $file) {
        Write-Host "  ✓ $file" -ForegroundColor Green
    } else {
        Write-Host "  ✗ $file が見つかりません" -ForegroundColor Red
        $allFilesExist = $false
    }
}

if (-not $allFilesExist) {
    Write-Host ""
    Write-Host "必要なファイルが不足しています" -ForegroundColor Red
    exit
}

# サーバー起動
Write-Host ""
Write-Host "[4/4] サーバーを起動中..." -ForegroundColor Yellow
Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "サーバー情報" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  URL: http://localhost:8002" -ForegroundColor White
Write-Host "  エンジン: Playwright" -ForegroundColor White
Write-Host "  並列数: 3 (ページプール)" -ForegroundColor White
Write-Host "  終了: Ctrl+C" -ForegroundColor White
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# サーバー起動
python scraping_service_playwright.py
