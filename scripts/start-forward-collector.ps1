param(
    [string]$RuntimeDirectory = 'reports/generated/forward-runtime/h04-live-20260920-v2',
    [switch]$ResumeStopped
)
$ErrorActionPreference = 'Stop'
$forwardRepo = Split-Path -Parent $PSScriptRoot
$forwardRoot = [IO.Path]::GetFullPath((Join-Path $forwardRepo $RuntimeDirectory))
$forwardPython = Join-Path $forwardRepo 'python-api\.venv\Scripts\python.exe'
$forwardScript = Join-Path $PSScriptRoot 'run_forward_collector.py'
if (-not (Test-Path -LiteralPath (Join-Path $forwardRoot 'protocol.json'))) { throw 'Initialize and validate the paper runtime first.' }
$forwardOut = Join-Path $forwardRoot ('collector-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.stdout.log')
$forwardErr = $forwardOut.Replace('.stdout.log', '.stderr.log')
$forwardArgs = @('-u', ('"' + $forwardScript + '"'), 'run', '--root', ('"' + $forwardRoot + '"'), '--allow-network')
if ($ResumeStopped) { $forwardArgs += '--resume-stopped' }
$env:PYTHONIOENCODING = 'utf-8'
$forwardProcess = Start-Process -FilePath $forwardPython -ArgumentList $forwardArgs -WorkingDirectory $forwardRepo -WindowStyle Hidden -RedirectStandardOutput $forwardOut -RedirectStandardError $forwardErr -PassThru
Write-Output ('Paper collector dispatched. PID=' + $forwardProcess.Id)
Write-Output 'Use the Stop command below; do not terminate Python processes by name.'
Write-Output ('Status: python-api\.venv\Scripts\python.exe scripts/run_forward_collector.py status --root "' + $forwardRoot + '"')
Write-Output ('Stop: python-api\.venv\Scripts\python.exe scripts/run_forward_collector.py stop --root "' + $forwardRoot + '"')
Write-Output ('Logs: ' + $forwardOut)
