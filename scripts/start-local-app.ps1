[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$DevelopmentFrontend,
    [switch]$Restart,
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
# Hold this handle until the launcher exits, including failed startup paths.
$LauncherMutex = New-Object System.Threading.Mutex($false, 'Local\KeibaAIProLocalLauncher')
try {
    $LaunchAcquired = $LauncherMutex.WaitOne(0)
}
catch [System.Threading.AbandonedMutexException] {
    $LaunchAcquired = $true
}
if (-not $LaunchAcquired) {
    Write-Host 'Keiba AI Pro startup is already in progress. No additional servers were started.'
    exit 0
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

function Get-ApiHealthPayload {
    try {
        return Invoke-RestMethod -Uri $ApiHealthUrl -TimeoutSec 4
    }
    catch {
        return $null
    }
}

function Test-PredictionRecommendationRuntimeReady {
    $health = Get-ApiHealthPayload
    return $null -ne $health -and
        $health.recommendation_runtime.ready -eq $true -and
        'value' -in $health.recommendation_runtime.supported_strategies -and
        'fixed-two-v1' -in $health.recommendation_runtime.supported_strategies
}

function Test-OperationalScrapeRuntimeReady {
    $health = Get-ApiHealthPayload
    return $null -ne $health -and
        $health.app_env -in @('local', 'development', 'dev') -and
        $health.operational_scrape_runtime.mode -eq 'local-sqlite' -and
        $health.operational_scrape_runtime.enabled -eq $true -and
        $health.operational_scrape_runtime.worker_enabled -eq $true
}

function Test-OperationalScrapeRuntimeNeedsRestart {
    $health = Get-ApiHealthPayload
    $runtime = if ($null -ne $health) { $health.operational_scrape_runtime } else { $null }
    return $null -ne $health -and
        $health.app_env -in @('local', 'development', 'dev') -and
        ($null -eq $runtime -or (
            $runtime.mode -eq 'disabled' -and $runtime.enabled -eq $false
        ))
}

function Find-RepositoryApiProcess([string]$ExpectedPythonExe) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processId = [int]$listener.OwningProcess
        for ($depth = 0; $depth -lt 6 -and $processId -gt 0; $depth++) {
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
            if ($null -eq $processInfo) { break }
            $repositoryMain = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'python-api\main.py'))
            $knownCommand = ($processInfo.CommandLine -match '(?i)-m\s+uvicorn\s+main:app' -and
                $processInfo.CommandLine -match '(?i)--port\s+8000') -or
                $processInfo.CommandLine -match '(?i)(?:^|\s)"?main\.py"?(?:\s|$)' -or
                ($processInfo.CommandLine -and $processInfo.CommandLine.IndexOf($repositoryMain, [StringComparison]::OrdinalIgnoreCase) -ge 0)
            if ($processInfo.ExecutablePath -ieq $ExpectedPythonExe -and $knownCommand) {
                return Get-Process -Id $processId -ErrorAction SilentlyContinue
            }
            $processId = [int]$processInfo.ParentProcessId
        }
    }
    return $null
}

function Find-RepositoryWebProcess([string]$ExpectedNodeExe) {
    $expectedServer = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'node_modules\next\dist\server\lib\start-server.js'))
    $expectedStandaloneServer = [IO.Path]::GetFullPath((Join-Path $RepoRoot '.next\standalone\server.js'))
    $expectedCli = [IO.Path]::GetFullPath((Join-Path $RepoRoot 'node_modules\next\dist\bin\next'))
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processId = [int]$listener.OwningProcess
        for ($depth = 0; $depth -lt 6 -and $processId -gt 0; $depth++) {
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
            if ($null -eq $processInfo) { break }
            if ($processInfo.ExecutablePath -ieq $ExpectedNodeExe -and
                $processInfo.CommandLine -and
                ($processInfo.CommandLine.IndexOf($expectedServer, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                 $processInfo.CommandLine.IndexOf($expectedStandaloneServer, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                 $processInfo.CommandLine.IndexOf(('"' + $expectedCli + '" start '), [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                return Get-Process -Id $processId -ErrorAction SilentlyContinue
            }
            $processId = [int]$processInfo.ParentProcessId
        }
    }
    return $null
}

function Test-LocalLoginRouteReady {
    # A 307 with this exact failure reason proves that the dev-only handler is
    # active and its configuration loaded, without sending or logging a token.
    $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:3000/api/local-login')
    $request.Method = 'GET'
    $request.AllowAutoRedirect = $false
    $request.Timeout = 4000
    $response = $null
    try {
        $response = $request.GetResponse()
    }
    catch [Net.WebException] {
        $response = $_.Exception.Response
    }
    if ($null -eq $response) { return $false }
    try {
        $location = [string]$response.Headers['Location']
        return [int]$response.StatusCode -eq 307 -and
            $location -match '(?i)/login\?local_auto_login=token_rejected(?:&|$)'
    }
    finally {
        $response.Dispose()
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
    $deadline = (Get-Date).AddSeconds(10)
    $listeners = @()
    do {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
        if (-not $listeners -or (Test-Endpoint -Url $HealthUrl)) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners -and -not (Test-Endpoint -Url $HealthUrl)) {
        $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is still used by PID $owners, but $HealthUrl did not become healthy after waiting 10 seconds. The launcher did not stop the existing process. Check its owner, stop it only if it is an obsolete Keiba AI Pro process, then retry."
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

function Get-LocalLoginToken([string]$Path) {
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

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
$PreviousState = $null
if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
    try {
        $PreviousState = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "Launcher state could not be read safely: $StatePath. No existing process was stopped."
    }
}
$env:LOCAL_CONFIGURATION_ROOT = $ConfigRoot
$env:PYTHONUTF8 = '1'
$LoginToken = Get-LocalLoginToken -Path (Join-Path $ConfigRoot '.env.test')
$AppUrl = 'http://127.0.0.1:3000/api/local-login?token=' + [Uri]::EscapeDataString($LoginToken)

$PythonExe = Join-Path $RepoRoot 'python-api\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw 'Python virtual environment was not found. Run npm run setup:api.'
}

$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $NpmCommand) {
    throw 'npm.cmd was not found. Install Node.js 24.x.'
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

if ($Restart) {
    $health = Get-ApiHealthPayload
    if ($health -and $health.prediction_runtime.active_workers -gt 0) {
        throw 'Prediction is running. Wait for it to finish before restarting.'
    }
    $NodeCommand = Get-Command node.exe -ErrorAction Stop
    $restartTargets = @()
    foreach ($port in @(8000, 3000)) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
            continue
        }
        $target = if ($port -eq 8000) {
            Find-RepositoryApiProcess -ExpectedPythonExe $PythonExe
        } else {
            Find-RepositoryWebProcess -ExpectedNodeExe $NodeCommand.Source
        }
        if ($null -eq $target) {
            throw "Port $port is not owned by a verified server from this repository, or Windows denied process inspection. No server was stopped. If Keiba AI Pro was started as administrator, right-click start-keiba-ai-pro.bat and choose Run as administrator."
        }
        $restartTargets += $target
    }
    foreach ($target in $restartTargets) {
        Write-Step "Stopping verified Keiba AI Pro server PID $($target.Id)..."
        Stop-StartedProcessTree -Process $target
    }
    if ($restartTargets.Count -gt 0) {
        $NoBrowser = $true
        Write-Step "Reusing your existing browser tab. Reload $DisplayUrl after startup."
    }
    $deadline = (Get-Date).AddSeconds(15)
    while (@(Get-NetTCPConnection -State Listen -LocalPort 3000,8000 -ErrorAction SilentlyContinue).Count -gt 0) {
        if ((Get-Date) -ge $deadline) { throw 'Application ports did not become free after stopping the verified servers.' }
        Start-Sleep -Milliseconds 500
    }
    $PreviousState = $null
    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force }
}

Assert-PortAvailableOrHealthy -Port 8000 -HealthUrl $ApiHealthUrl

# Load local configuration into child processes without printing values.
Import-DotEnv -Path (Join-Path $ConfigRoot '.env')
Import-DotEnv -Path (Join-Path $ConfigRoot '.env.local')
Import-DotEnv -Path (Join-Path $ConfigRoot 'python-api\.env')

# Local mode is deliberately fail-closed for automatic jobs and real betting.
$env:PYTHONPATH = $RepoRoot
$env:APP_ENV = 'development'
$env:PREDICTION_SOURCE_TRACKING_LOCAL_ENABLED = 'true'
$env:LOCAL_ADMIN_SESSION_ENABLED = 'true'
$env:MODEL_TRAINING_LOCAL_ENABLED = 'true'
$env:MODEL_ACTIVATION_LOCAL_ENABLED = 'true'
$env:MODEL_DELETION_LOCAL_ENABLED = 'true'
$env:API_HOST = '127.0.0.1'
$env:HOSTNAME = '127.0.0.1'
$env:NEXT_STANDALONE_BUILD = '0'
$env:SCHEDULER_ENABLED = 'false'
$env:PREDICTION_CPU_WORKERS = '1'
$env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:SUPABASE_DATA_ENABLED = 'false'
$env:NETKEIBA_RACE_WRITE_ENABLED = 'false'
$env:ALLOW_STAGING_WRITE = 'false'
$env:MODEL_RUNTIME_STATUS = 'observation'
$env:PHASE3N_OPERATIONAL_SAGA_MODE = 'local-sqlite'
$env:PHASE3N_SAGA_SQLITE_PATH = Join-Path ([IO.Path]::GetTempPath()) 'keiba-ai-pro\operational-saga.sqlite3'
$env:PHASE3N_WORKER_ENABLED = 'true'
$env:PHASE3N_POLL_INTERVAL_MS = '5000'
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

# Only reuse an existing app when its development-only local-login handler is
# active too. A healthy /api/health proxy alone can belong to a stale Next.js
# process started without the launcher's local environment.
if ((Test-Endpoint -Url $ApiHealthUrl) -and
    (Test-Endpoint -Url $WebHealthUrl) -and
    (Test-OperationalScrapeRuntimeReady) -and
    (Test-PredictionRecommendationRuntimeReady) -and
    (Test-LocalLoginRouteReady)) {
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

$ApiProcess = $null
$WebProcess = $null
$ApiStarted = $false
$WebStarted = $false
$FrontendMode = 'existing'
$StartedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
if ($PreviousState -and $PreviousState.startedAtUtc -and
    (($PreviousState.apiStartedByLauncher -and $PreviousState.apiProcessId) -or
     ($PreviousState.webStartedByLauncher -and $PreviousState.webProcessId))) {
    try { $StartedAtUtc = [datetime]::Parse($PreviousState.startedAtUtc).ToUniversalTime().ToString('o') }
    catch { throw "Launcher state has an invalid start time: $StatePath. No existing process was stopped." }
}

try {
    if (Test-Endpoint -Url $ApiHealthUrl) {
        if ((Test-OperationalScrapeRuntimeReady) -and (Test-PredictionRecommendationRuntimeReady)) {
            Write-Step 'FastAPI is already running with the local scrape runtime; reusing it.'
        }
        elseif ((Test-OperationalScrapeRuntimeNeedsRestart) -or -not (Test-PredictionRecommendationRuntimeReady)) {
            $ExistingApiProcess = Find-RepositoryApiProcess -ExpectedPythonExe $PythonExe
            if ($null -eq $ExistingApiProcess) {
                throw 'FastAPI is healthy but its prediction or scrape runtime is outdated. The port owner is not the verified repository Python server, so it was not stopped. Stop that server only after confirming its owner, then run this launcher again.'
            }
            Write-Step 'Restarting this repository API because its prediction or scrape runtime is outdated...'
            Stop-StartedProcessTree -Process $ExistingApiProcess
            $releaseDeadline = (Get-Date).AddSeconds(15)
            do {
                $remainingListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)
                if (-not $remainingListeners) { break }
                Start-Sleep -Milliseconds 500
            } while ((Get-Date) -lt $releaseDeadline)
            if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
                throw 'Port 8000 did not become free after stopping the verified local API process.'
            }
            Write-Step 'Starting FastAPI with the configured local scrape runtime...'
            $ApiProcess = Start-Process -FilePath $PythonExe -ArgumentList 'main.py' `
                -WorkingDirectory (Join-Path $RepoRoot 'python-api') -WindowStyle Hidden `
                -RedirectStandardOutput $ApiStdout -RedirectStandardError $ApiStderr -PassThru
            $ApiStarted = $true
        }
        else {
            throw 'FastAPI is healthy but did not report a verified local scrape runtime. The existing server was left untouched.'
        }
    }
    else {
        Write-Step 'Starting FastAPI on port 8000...'
        $ApiProcess = Start-Process -FilePath $PythonExe -ArgumentList 'main.py' `
            -WorkingDirectory (Join-Path $RepoRoot 'python-api') -WindowStyle Hidden `
            -RedirectStandardOutput $ApiStdout -RedirectStandardError $ApiStderr -PassThru
        $ApiStarted = $true
    }
    Wait-Endpoint -Name 'FastAPI' -Url $ApiHealthUrl -TimeoutSeconds $ApiTimeoutSeconds -StartedProcess $ApiProcess
    if (-not (Test-PredictionRecommendationRuntimeReady)) {
        throw 'FastAPI responded, but both betting strategies are not available in the loaded API. Restart the API with the current source.'
    }
    if (-not (Test-OperationalScrapeRuntimeReady)) {
        throw 'FastAPI responded, but the local operational scrape runtime or worker is not enabled. Check the API startup logs.'
    }

    # The frontend health endpoint depends on FastAPI. During an API-only
    # restart it can legitimately return 503 until the backend is ready.
    Assert-PortAvailableOrHealthy -Port 3000 -HealthUrl $WebHealthUrl
    $StartWeb = -not (Test-Endpoint -Url $WebHealthUrl)
    if (-not $StartWeb) {
        if (Test-LocalLoginRouteReady) {
            Write-Step 'Next.js is already running with local login enabled; reusing it.'
        }
        else {
            $NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
            $ExistingWebProcess = if ($NodeCommand) {
                Find-RepositoryWebProcess -ExpectedNodeExe $NodeCommand.Source
            } else { $null }
            if ($null -eq $ExistingWebProcess) {
                throw 'Next.js is healthy, but the local-login route is unavailable and the port owner is not a verified Next.js process from this repository. It was left untouched; check port 3000 and logs before retrying.'
            }
            Write-Step 'Restarting this repository Next.js because its local-login route is disabled or stale...'
            Stop-StartedProcessTree -Process $ExistingWebProcess
            $webReleaseDeadline = (Get-Date).AddSeconds(15)
            do {
                $remainingWebListeners = @(Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue)
                if (-not $remainingWebListeners) { break }
                Start-Sleep -Milliseconds 500
            } while ((Get-Date) -lt $webReleaseDeadline)
            if (Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue) {
                throw 'Port 3000 did not become free after stopping the verified repository Next.js process.'
            }
            $StartWeb = $true
        }
    }
    if ($StartWeb) {
        $WebCommand = 'npm run dev -- --hostname 127.0.0.1'
        $FrontendMode = 'development'
        if (-not $DevelopmentFrontend) {
            if (-not (Test-ProductionBuildCurrent)) {
                Write-Step 'Building the production frontend (first run or source changed)...'
                $BuildProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/s', '/c', 'npm run build -- --webpack' `
                    -WorkingDirectory $RepoRoot -WindowStyle Hidden `
                    -RedirectStandardOutput $WebStdout -RedirectStandardError $WebStderr -PassThru -Wait
                if ($BuildProcess.ExitCode -ne 0) {
                    throw "Next.js production build failed. Check $WebStderr"
                }
            }
            $env:NODE_OPTIONS = '--max-old-space-size=2048'
            $WebCommand = 'node "' + (Join-Path $RepoRoot 'node_modules\next\dist\bin\next') + '" start --hostname 127.0.0.1'
            $FrontendMode = 'production'
        }
        Write-Step "Starting Next.js ($FrontendMode) on port 3000..."
        $WebProcess = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/s', '/c', $WebCommand `
            -WorkingDirectory $RepoRoot -WindowStyle Hidden `
            -RedirectStandardOutput $WebStdout -RedirectStandardError $WebStderr -PassThru
        $WebStarted = $true
    }
    Wait-Endpoint -Name 'Next.js' -Url $WebHealthUrl -TimeoutSeconds $WebTimeoutSeconds -StartedProcess $WebProcess
    if (-not (Test-LocalLoginRouteReady)) {
        throw "Next.js is healthy, but its local-login route is not ready. Check .env.local and .env.test configuration and logs in $RuntimeDir"
    }

    $state = [ordered]@{
        version = 1
        repoRoot = $RepoRoot
        startedAtUtc = $StartedAtUtc
        apiProcessId = if ($ApiStarted) { $ApiProcess.Id } elseif ($PreviousState -and $PreviousState.apiStartedByLauncher -and $PreviousState.apiProcessId -and (Get-Process -Id ([int]$PreviousState.apiProcessId) -ErrorAction SilentlyContinue)) { $PreviousState.apiProcessId } else { $null }
        webProcessId = if ($WebStarted) { $WebProcess.Id } elseif ($PreviousState -and $PreviousState.webStartedByLauncher -and $PreviousState.webProcessId -and (Get-Process -Id ([int]$PreviousState.webProcessId) -ErrorAction SilentlyContinue)) { $PreviousState.webProcessId } else { $null }
        apiStartedByLauncher = $ApiStarted -or ($PreviousState -and $PreviousState.apiStartedByLauncher -and $PreviousState.apiProcessId -and (Get-Process -Id ([int]$PreviousState.apiProcessId) -ErrorAction SilentlyContinue))
        webStartedByLauncher = $WebStarted -or ($PreviousState -and $PreviousState.webStartedByLauncher -and $PreviousState.webProcessId -and (Get-Process -Id ([int]$PreviousState.webProcessId) -ErrorAction SilentlyContinue))
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
