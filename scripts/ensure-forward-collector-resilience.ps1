param(
    [switch]$Launch,
    [switch]$Audit,
    [string]$ExpectedWrapperSha = ''
)
$ErrorActionPreference = 'Stop'
$forwardRepo = Split-Path -Parent $PSScriptRoot
$forwardGuard = Join-Path $PSScriptRoot 'forward_startup_guard.py'
$forwardPython = Join-Path $forwardRepo 'python-api\.venv\Scripts\python.exe'
$forwardRoot = Join-Path $forwardRepo 'reports\generated\forward-runtime\h04-live-20260920-v2'
$forwardGuardSha = '9ceb9c9dd613005f6f574319a18f77da71e3c0f0cc19bdd4dc6696cd2208a5a2'
if ((Get-FileHash -LiteralPath $forwardGuard -Algorithm SHA256).Hash.ToLowerInvariant() -ne $forwardGuardSha) {
    throw 'External guard changed; inspect it and explicitly revise the separate startup task pin.'
}
if ($Launch) {
    if ($ExpectedWrapperSha -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedWrapperSha) {
        throw 'Explicit launch requires the Task Scheduler pinned wrapper hash.'
    }
}
$forwardArgs = @('-u', $forwardGuard, '--root', $forwardRoot,
    '--expected-protocol', '97eeca6207cbf9e8c11816e29c47fc5a71761a63b41e2aea3e4cee4245235307',
    '--expected-analysis', '3373a7e00aabcb3ce8c390466c5d755cebe1b413f95c936aee58c2b78264b393',
    '--expected-launcher', '3981ac371403ccf171615151f1376579f123b1be8e1a42aba6ce230f41ad03e7')
if ($Audit) { $forwardArgs += '--audit' }
if ($Launch) { $forwardArgs += '--launch' }
$env:PYTHONIOENCODING = 'utf-8'
$forwardText = (& $forwardPython @forwardArgs 2>&1 | Out-String)
$forwardExit = $LASTEXITCODE
if ($Launch) {
    $forwardLogs = Join-Path $forwardRepo 'reports\generated\forward-runtime-ops\h04-live-20260920-v2'
    New-Item -ItemType Directory -Path $forwardLogs -Force | Out-Null
    try {
        $forwardResult = $forwardText | ConvertFrom-Json
        $forwardLog = [ordered]@{ observed_at = [DateTime]::UtcNow.ToString('o'); operation = $forwardResult.operation;
            launched = $forwardResult.launched; stop_latch_cleared = $forwardResult.stop_latch_cleared;
            full_preflight_performed = $forwardResult.full_preflight_performed; error = $forwardResult.error; exit_code = $forwardExit }
    } catch {
        $forwardLog = [ordered]@{ observed_at = [DateTime]::UtcNow.ToString('o'); operation = 'guard-output-invalid';
            exit_code = $forwardExit; detail = $forwardText.Substring(0, [Math]::Min(1000, $forwardText.Length)) }
    }
    $forwardLog | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $forwardLogs ('guard-' + (Get-Date -Format 'yyyyMMdd') + '.jsonl')) -Encoding UTF8
}
Write-Output $forwardText
exit $forwardExit
