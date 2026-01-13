# 開発環境の起動スクリプト

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  競馬AI Pro 開発環境起動" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# プロジェクトディレクトリに移動
Set-Location $PSScriptRoot

Write-Host "起動するサービス:" -ForegroundColor Yellow
Write-Host "  - Next.js開発サーバー (http://localhost:3000)" -ForegroundColor White
Write-Host "  - Python APIサーバー (http://localhost:8001)" -ForegroundColor White
Write-Host ""

# 環境変数のPATHを更新（npmコマンドが見つからない問題を回避）
$MachinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
$UserPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = $MachinePath + ";" + $UserPath

# 両方のサーバーを同時起動
Write-Host "起動中..." -ForegroundColor Cyan
Write-Host ""
npm run dev:all
