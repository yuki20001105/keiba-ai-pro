# Stop all servers script

Write-Host "=================================" -ForegroundColor Cyan
Write-Host "  Stopping servers..." -ForegroundColor Cyan
Write-Host "=================================" -ForegroundColor Cyan
Write-Host ""

# Stop Node.js (Next.js) and Python (FastAPI) processes
$processes = Get-Process node,python -ErrorAction SilentlyContinue

if ($processes) {
    Write-Host "[*] Found running processes" -ForegroundColor Yellow
    
    foreach ($proc in $processes) {
        Write-Host "  - $($proc.ProcessName) (PID: $($proc.Id))" -ForegroundColor Gray
    }
    
    Write-Host ""
    Write-Host "Stopping..." -ForegroundColor Yellow
    
    $processes | Stop-Process -Force -ErrorAction SilentlyContinue
    
    Start-Sleep -Seconds 2
    
    Write-Host ""
    Write-Host "[OK] All servers stopped" -ForegroundColor Green
} else {
    Write-Host "[i] No servers running" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=================================" -ForegroundColor Cyan
Write-Host "  Complete!" -ForegroundColor Green
Write-Host "=================================" -ForegroundColor Cyan
