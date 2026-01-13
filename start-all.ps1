# Keiba AI - Start All Services
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Starting Keiba AI Services" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# FastAPIサーバーを起動
Write-Host "Starting FastAPI Server..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\python-api'; .\.venv\Scripts\python.exe main.py" -WindowStyle Normal

# 少し待機
Start-Sleep -Seconds 3

# Next.jsサーバーを起動
Write-Host "Starting Next.js Server..." -ForegroundColor Yellow
$env:PATH = "C:\Program Files\nodejs;$env:PATH"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; npm run dev" -WindowStyle Normal

Write-Host ""
Write-Host "=====================================" -ForegroundColor Green
Write-Host "Both servers are starting!" -ForegroundColor Green
Write-Host "FastAPI: http://localhost:8000" -ForegroundColor White
Write-Host "Next.js: http://localhost:3000" -ForegroundColor White
Write-Host "=====================================" -ForegroundColor Green
Write-Host ""
Write-Host "Press any key to exit (servers will keep running)..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
