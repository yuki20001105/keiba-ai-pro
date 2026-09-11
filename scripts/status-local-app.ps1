[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ApiUrl = 'http://127.0.0.1:8000/health'
$WebUrl = 'http://127.0.0.1:3000/api/health'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$StatePath = Join-Path $RepoRoot 'logs\local-app\state.json'

function Test-Endpoint([string]$Name, [string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Host "[OK] $Name - $Url" -ForegroundColor Green
            return $true
        }
    }
    catch {}
    Write-Host "[NG] $Name - $Url" -ForegroundColor Red
    return $false
}

$apiOk = Test-Endpoint -Name 'FastAPI' -Url $ApiUrl
$webOk = Test-Endpoint -Name 'Next.js + backend proxy' -Url $WebUrl

if ($apiOk) {
    try {
        $health = Invoke-RestMethod -Uri $ApiUrl -TimeoutSec 5
        $long = $health.long_running_jobs
        if ($null -ne $long) {
            $resource = $long.resource
            $resourceText = "FastAPI RSS=$($resource.process_rss_mb) MB / system available=$($resource.system_available_mb) MB"
            Write-Host "[LONG JOB] auto-resume=$($long.auto_resume_enabled), active=$($long.active_worker_count), recoverable=$($long.recoverable_job_count), stale=$($long.stale_job_count)"
            Write-Host "[RESOURCE] $resourceText"
            if ([int]$long.stale_job_count -gt 0) {
                Write-Host '[WARNING] A long-running job heartbeat is stale. Review logs\local-app\fastapi.stderr.log.' -ForegroundColor Yellow
            }
            if ($long.resource_capacity_available -eq $false) {
                Write-Host '[WAITING] Resource guard is active. The job will continue when memory becomes available.' -ForegroundColor Yellow
            }
        }
    }
    catch {
        Write-Host '[WARNING] Long-running job health details could not be read.' -ForegroundColor Yellow
    }
}

if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host "[FRONTEND] mode=$($state.frontendMode) (production mode is recommended for long runs)"
    }
    catch {}
}

if ($apiOk -and $webOk) {
    Write-Host 'Keiba AI Pro is ready at http://127.0.0.1:3000/login' -ForegroundColor Cyan
    exit 0
}

Write-Host 'Run start-keiba-ai-pro.bat from the repository root.' -ForegroundColor Yellow
exit 1
