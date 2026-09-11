[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$RuntimeDir = Join-Path $RepoRoot 'logs\local-app'
$StatePath = Join-Path $RuntimeDir 'state.json'

function Get-ChildProcessIds([int]$ParentId) {
    @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty ProcessId)
}

function Stop-RecordedTree([int]$ProcessId, [datetime]$EarliestStartUtc) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return }
    if ($process.StartTime.ToUniversalTime() -lt $EarliestStartUtc.AddSeconds(-15)) {
        Write-Warning "PID $ProcessId was reused by another process; it was not stopped."
        return
    }
    foreach ($childId in (Get-ChildProcessIds -ParentId $ProcessId)) {
        Stop-RecordedTree -ProcessId ([int]$childId) -EarliestStartUtc $EarliestStartUtc
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    Write-Host 'No launcher state was found. No process was stopped.' -ForegroundColor Yellow
    Write-Host "State path: $StatePath"
    exit 0
}

$state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$startedAtUtc = [datetime]::Parse($state.startedAtUtc).ToUniversalTime()

if ($state.webStartedByLauncher -and $state.webProcessId) {
    Stop-RecordedTree -ProcessId ([int]$state.webProcessId) -EarliestStartUtc $startedAtUtc
}
if ($state.apiStartedByLauncher -and $state.apiProcessId) {
    Stop-RecordedTree -ProcessId ([int]$state.apiProcessId) -EarliestStartUtc $startedAtUtc
}

Remove-Item -LiteralPath $StatePath -Force
Write-Host 'Keiba AI Pro processes started by the launcher were stopped.' -ForegroundColor Green
Write-Host 'Unrelated Node.js and Python processes were not touched.'
exit 0
