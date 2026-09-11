[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$DevelopmentFrontend,
    [string]$RuntimeRepoRoot,
    [string]$ConfigurationRoot,
    [int]$ApiTimeoutSeconds = 120,
    [int]$WebTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

$LauncherRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$RepoRoot = if ($RuntimeRepoRoot) {
    [IO.Path]::GetFullPath($RuntimeRepoRoot)
} else {
    $LauncherRoot
}
$ConfigRoot = if ($ConfigurationRoot) {
    [IO.Path]::GetFullPath($ConfigurationRoot)
} else {
    $RepoRoot
}
$RuntimeDir = Join-Path $LauncherRoot 'logs\local-app'
$StatePath = Join-Path $RuntimeDir 'state.json'
$ApiStdout = Join-Path $RuntimeDir 'fastapi.stdout.log'
$ApiStderr = Join-Path $RuntimeDir 'fastapi.stderr.log'
$WebStdout = Join-Path $RuntimeDir 'next.stdout.log'
$WebStderr = Join-Path $RuntimeDir 'next.stderr.log'
$ApiHealthUrl = 'http://127.0.0.1:8000/health'
$WebHealthUrl = 'http://127.0.0.1:3000/api/health'
$DisplayUrl = 'http://127.0.0.1:3000/home'

function Write-Step([string]$Message) {
    Write-Host "[Keiba AI Pro] $Message" -ForegroundColor Cyan
}

function Test-Endpoint([string]$Url, [int]$TimeoutSeconds = 4) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Wait-Endpoint(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds,
    [System.Diagnostics.Process]$StartedProcess
) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint -Url $Url) {
            Write-Host "  OK: $Name is ready ($Url)" -ForegroundColor Green
            return
        }
        if ($null -ne $StartedProcess) {
            $StartedProcess.Refresh()
            if ($StartedProcess.HasExited) {
                throw "$Name exited before becoming ready. Check logs in $RuntimeDir"
            }
        }
        Start-Sleep -Seconds 1
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds. Check logs in $RuntimeDir"
}

function Assert-PortAvailableOrHealthy([int]$Port, [string]$HealthUrl) {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listeners -and -not (Test-Endpoint -Url $HealthUrl)) {
        $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is already used by PID $owners, but $HealthUrl is not healthy. Stop that process first."
    }
}

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            continue
        }
        $name = $matches[1]
        $value = $matches[2].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        Set-Item -Path "Env:$name" -Value $value
    }
}

function Get-OrCreate-LocalAutoLoginToken([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw '.env.test was not found. Configure E2E_EMAIL and E2E_PASSWORD for local auto-login.'
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*LOCAL_AUTO_LOGIN_TOKEN\s*=\s*(.+?)\s*$') {
            $value = $matches[1].Trim().Trim('"').Trim("'")
            if ($value.Length -ge 32) { return $value }
        }
    }

    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    Add-Content -LiteralPath $Path -Value "`r`nLOCAL_AUTO_LOGIN_TOKEN=$token" -Encoding UTF8
    return $token
}

function Stop-StartedProcessTree([System.Diagnostics.Process]$Process) {
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            $childIds = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($Process.Id)" -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty ProcessId)
            foreach ($childId in $childIds) {
                $child = Get-Process -Id ([int]$childId) -ErrorAction SilentlyContinue
                Stop-StartedProcessTree -Process $child
            }
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {}
}

function Test-ProductionBuildCurrent {
    $buildMarker = Join-Path $RepoRoot '.next\BUILD_ID'
    if (-not (Test-Path -LiteralPath $buildMarker -PathType Leaf)) {
        return $false
    }
    $buildTime = (Get-Item -LiteralPath $buildMarker).LastWriteTimeUtc
    $roots = @('src', 'public') | ForEach-Object { Join-Path $RepoRoot $_ }
    $latestSource = Get-ChildItem -LiteralPath $roots -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $configPaths = @(
        'package.json', 'package-lock.json', 'next.config.js', 'next.config.mjs',
        'tsconfig.json', '.env', '.env.local'
    ) | ForEach-Object { Join-Path $RepoRoot $_ } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    $latestConfig = $configPaths | ForEach-Object { Get-Item -LiteralPath $_ } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $latestInputTime = @($latestSource, $latestConfig) |
        Where-Object { $null -ne $_ } |
        ForEach-Object { $_.LastWriteTimeUtc } |
        Sort-Object -Descending |
        Select-Object -First 1
    return $null -eq $latestInputTime -or $buildTime -ge $latestInputTime
}

function Sync-StandaloneAssets {
    $standaloneRoot = Join-Path $RepoRoot '.next\standalone'
    $serverPath = Join-Path $standaloneRoot 'server.js'
    if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) {
        throw 'Next.js standalone server was not generated. Run npm run build and review next.config.js.'
    }
    $staticSource = Join-Path $RepoRoot '.next\static'
    if (Test-Path -LiteralPath $staticSource -PathType Container) {
        $staticTarget = Join-Path $standaloneRoot '.next\static'
        New-Item -ItemType Directory -Path $staticTarget -Force | Out-Null
        Copy-Item -Path (Join-Path $staticSource '*') -Destination $staticTarget -Recurse -Force
    }
    $publicSource = Join-Path $RepoRoot 'public'
    if (Test-Path -LiteralPath $publicSource -PathType Container) {
        $publicTarget = Join-Path $standaloneRoot 'public'
        New-Item -ItemType Directory -Path $publicTarget -Force | Out-Null
        Copy-Item -Path (Join-Path $publicSource '*') -Destination $publicTarget -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
$env:LOCAL_CONFIGURATION_ROOT = $ConfigRoot
$env:PYTHONUTF8 = '1'
$LocalAutoLoginToken = Get-OrCreate-LocalAutoLoginToken -Path (Join-Path $ConfigRoot '.env.test')
$AppUrl = 'http://127.0.0.1:3000/api/local-login?token=' + [Uri]::EscapeDataString($LocalAutoLoginToken)

# A second double-click must not overwrite the launcher state; otherwise the
# stop BAT would lose the PIDs of the already-running processes.
if ((Test-Endpoint -Url $ApiHealthUrl) -and (Test-Endpoint -Url $WebHealthUrl)) {
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        Write-Host 'Keiba AI Pro is already running.' -ForegroundColor Green
    }
    else {
        Write-Host 'Keiba AI Pro is already running outside this launcher.' -ForegroundColor Yellow
        Write-Host 'The existing processes were not adopted and will not be stopped by stop-keiba-ai-pro.bat.'
    }
    Write-Host "  App: $DisplayUrl"
    if (-not $NoBrowser) {
        Start-Process $AppUrl
    }
    exit 0
}

$pythonCandidates = @(
    (Join-Path $RepoRoot 'python-api\.venv\Scripts\python.exe'),
    (Join-Path $RepoRoot '.venv\Scripts\python.exe')
)
$PythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $PythonExe) {
    throw 'Python virtual environment was not found. Create python-api\.venv and install python-api\requirements-lock.txt.'
}

$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $NpmCommand) {
    throw 'npm.cmd was not found. Install Node.js 18.17 or later.'
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'node_modules\.bin\next.cmd'))) {
    throw 'Node dependencies were not found. Run npm ci in the repository root.'
}

$DatabasePath = Join-Path $RepoRoot 'keiba\data\keiba_ultimate.db'
if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf) -or (Get-Item -LiteralPath $DatabasePath).Length -eq 0) {
    throw 'Local research DB was not found: keiba\data\keiba_ultimate.db'
}
if (-not (Test-Path -LiteralPath (Join-Path $ConfigRoot '.env.local') -PathType Leaf)) {
    throw '.env.local was not found. Create it from .env.local.example and configure the approved Supabase values.'
}

Assert-PortAvailableOrHealthy -Port 8000 -HealthUrl $ApiHealthUrl
Assert-PortAvailableOrHealthy -Port 3000 -HealthUrl $WebHealthUrl

# Load local configuration into child processes without printing values.
Import-DotEnv -Path (Join-Path $ConfigRoot '.env')
Import-DotEnv -Path (Join-Path $ConfigRoot '.env.local')
Import-DotEnv -Path (Join-Path $ConfigRoot 'python-api\.env')

# Local mode is deliberately fail-closed for automatic jobs and real betting.
$env:PYTHONPATH = $RepoRoot
$env:APP_ENV = 'development'
$env:SCHEDULER_ENABLED = 'false'
$env:SUPABASE_DATA_ENABLED = 'false'
$env:NETKEIBA_RACE_WRITE_ENABLED = 'false'
$env:ALLOW_STAGING_WRITE = 'false'
$env:MODEL_RUNTIME_STATUS = 'observation'
$env:PHASE3N_OPERATIONAL_SAGA_MODE = 'local-sqlite'
$env:PHASE3N_SAGA_SQLITE_PATH = Join-Path ([IO.Path]::GetTempPath()) 'keiba-ai-pro\operational-saga.sqlite3'
$env:PHASE3N_WORKER_ENABLED = 'true'
$env:PHASE3N_REMOTE_EFFECTS_ENABLED = 'true'
$env:PHASE3N_EXECUTION_UNLOCK_ENABLED = 'true'
$env:ML_API_URL = 'http://127.0.0.1:8000'
$env:SCRAPE_API_URL = 'http://127.0.0.1:8000'
$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8000'
$env:NEXT_PUBLIC_APP_URL = 'http://127.0.0.1:3000'
$env:PORT = '3000'
foreach ($secretName in @('IPAT_INET_ID', 'IPAT_USER_ID', 'IPAT_PASSWORD', 'IPAT_PARS')) {
    Remove-Item -Path "Env:$secretName" -ErrorAction SilentlyContinue
}

$ApiProcess = $null
$WebProcess = $null
$ApiStarted = $false
$WebStarted = $false
$FrontendMode = 'existing'
$StartedAtUtc = (Get-Date).ToUniversalTime().ToString('o')

try {
    if (Test-Endpoint -Url $ApiHealthUrl) {
        Write-Step 'FastAPI is already running; reusing it.'
    }
    else {
        Write-Step 'Starting FastAPI on port 8000...'
        $ApiProcess = Start-Process -FilePath $PythonExe -ArgumentList 'main.py' `
            -WorkingDirectory (Join-Path $RepoRoot 'python-api') -WindowStyle Hidden `
            -RedirectStandardOutput $ApiStdout -RedirectStandardError $ApiStderr -PassThru
        $ApiStarted = $true
    }
    Wait-Endpoint -Name 'FastAPI' -Url $ApiHealthUrl -TimeoutSeconds $ApiTimeoutSeconds -StartedProcess $ApiProcess

    if (Test-Endpoint -Url $WebHealthUrl) {
        Write-Step 'Next.js is already running; reusing it.'
    }
    else {
        $WebCommand = 'npm run dev'
        $FrontendMode = 'development'
        if (-not $DevelopmentFrontend) {
            if (-not (Test-ProductionBuildCurrent)) {
                Write-Step 'Building the production frontend (first run or source changed)...'
                $BuildProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/s', '/c', 'npm run build' `
                    -WorkingDirectory $RepoRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $WebStdout -RedirectStandardError $WebStderr -PassThru -Wait
                if ($BuildProcess.ExitCode -ne 0) {
                    throw "Next.js production build failed. Check $WebStderr"
                }
            }
            $env:NODE_OPTIONS = '--max-old-space-size=2048'
            Sync-StandaloneAssets
            $WebCommand = 'node .next/standalone/server.js'
            $FrontendMode = 'production'
        }
        Write-Step "Starting Next.js ($FrontendMode) on port 3000..."
        $WebProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/s', '/c', $WebCommand `
            -WorkingDirectory $RepoRoot -WindowStyle Hidden `
            -RedirectStandardOutput $WebStdout -RedirectStandardError $WebStderr -PassThru
        $WebStarted = $true
    }
    Wait-Endpoint -Name 'Next.js' -Url $WebHealthUrl -TimeoutSeconds $WebTimeoutSeconds -StartedProcess $WebProcess

    $state = [ordered]@{
        version = 1
        repoRoot = $RepoRoot
        startedAtUtc = $StartedAtUtc
        apiProcessId = if ($ApiStarted) { $ApiProcess.Id } else { $null }
        webProcessId = if ($WebStarted) { $WebProcess.Id } else { $null }
        apiStartedByLauncher = $ApiStarted
        webStartedByLauncher = $WebStarted
        apiHealthUrl = $ApiHealthUrl
        webHealthUrl = $WebHealthUrl
        frontendMode = $FrontendMode
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8

    Write-Host ''
    Write-Host 'Keiba AI Pro is ready.' -ForegroundColor Green
    Write-Host "  App:     $DisplayUrl"
    Write-Host "  FastAPI: $ApiHealthUrl"
    Write-Host "  Logs:    $RuntimeDir"
    Write-Host '  Safety:  observation mode / scheduler off / real betting credentials removed' -ForegroundColor Yellow

    if (-not $NoBrowser) {
        Start-Process $AppUrl
    }
}
catch {
    Stop-StartedProcessTree -Process $WebProcess
    Stop-StartedProcessTree -Process $ApiProcess
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Logs: $RuntimeDir" -ForegroundColor Yellow
    exit 1
}

exit 0
