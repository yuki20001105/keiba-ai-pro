# PowerShell Test Script
Write-Host "`n=== 1. Ultimate Service Health Check ===" -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8001/health" -Method Get -TimeoutSec 5
    Write-Host "Status: $($response.status)" -ForegroundColor Green
    Write-Host "Cache size: $($response.cache_size)" -ForegroundColor Green
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
}

Write-Host "`n=== 2. Next.js Health Check ===" -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri "http://localhost:3000" -UseBasicParsing -TimeoutSec 5
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "Next.js is running" -ForegroundColor Green
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
}

Write-Host "`n=== 3. Race Data Scraping Test ===" -ForegroundColor Cyan
try {
    $body = @{
        race_id = "202401041001"
        include_details = $false
    } | ConvertTo-Json
    
    $response = Invoke-RestMethod -Uri "http://localhost:8001/scrape/ultimate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 60
    
    if ($response.success) {
        Write-Host "Race Name: $($response.race_info.race_name)" -ForegroundColor Green
        Write-Host "Distance: $($response.race_info.distance)m" -ForegroundColor Green
        Write-Host "Track: $($response.race_info.track_type)" -ForegroundColor Green
        Write-Host "Horses: $($response.results.Count)" -ForegroundColor Green
        
        if ($response.results.Count -gt 0) {
            $winner = $response.results[0]
            Write-Host "Winner: $($winner.horse_name) ($($winner.finish_time))" -ForegroundColor Green
        }
    } else {
        Write-Host "Scraping failed: $($response.error)" -ForegroundColor Red
    }
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
}

Write-Host "`n=== 4. Next.js Race List API Test ===" -ForegroundColor Cyan
try {
    $body = @{
        date = "2024-01-04"
    } | ConvertTo-Json
    
    $response = Invoke-RestMethod -Uri "http://localhost:3000/api/netkeiba/race-list" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    
    Write-Host "Found $($response.raceIds.Count) races" -ForegroundColor Green
    if ($response.raceIds.Count -gt 0) {
        Write-Host "Examples: $($response.raceIds[0..2] -join ', ')" -ForegroundColor Green
    }
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
}

Write-Host "`n=== Test Complete ===" -ForegroundColor Cyan
Write-Host "`nPress any key to continue..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
