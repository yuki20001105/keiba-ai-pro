param([switch]$Install)
$ErrorActionPreference = 'Stop'
$forwardRepo = Split-Path -Parent $PSScriptRoot
$forwardWrapper = Join-Path $PSScriptRoot 'ensure-forward-collector-resilience.ps1'
$forwardTaskName = 'KeibaAI-Forward-H04-20260920-v2-Resume'
$forwardIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$forwardWrapperSha = (Get-FileHash -LiteralPath $forwardWrapper -Algorithm SHA256).Hash.ToLowerInvariant()
$forwardActionArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $forwardWrapper + '" -Launch -ExpectedWrapperSha ' + $forwardWrapperSha
$forwardPlan = [ordered]@{
    task_name = $forwardTaskName; user = $forwardIdentity; logon_type = 'Interactive'; run_level = 'Limited';
    action = 'powershell.exe'; arguments = $forwardActionArgs; working_directory = $forwardRepo;
    triggers = @('Current-user logon', 'Every 5 minutes until 2026-12-19 00:00 JST');
    hidden = $true; multiple_instances = 'IgnoreNew'; execution_limit_minutes = 3;
    credential_stored = $false; reboot_performed = $false; stop_latch_cleared = $false;
    requires_user_logon_after_restart = $true; launches_now = $false; installed = $false
}
if (-not $Install) { $forwardPlan | ConvertTo-Json -Depth 5; exit 0 }
if (Get-ScheduledTask -TaskName $forwardTaskName -ErrorAction SilentlyContinue) {
    throw 'A task with this exact name already exists. Inspect it rather than overwriting or creating a duplicate.'
}
$forwardStart = (Get-Date).AddMinutes(1)
$forwardEnd = [DateTimeOffset]::Parse('2026-12-19T00:00:00+09:00').LocalDateTime
if ($forwardStart -ge $forwardEnd) { throw 'Fixed study has ended; no startup task will be registered.' }
$forwardLogon = New-ScheduledTaskTrigger -AtLogOn -User $forwardIdentity
$forwardRepeat = New-ScheduledTaskTrigger -Once -At $forwardStart -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ($forwardEnd - $forwardStart)
$forwardLogon.EndBoundary = $forwardEnd.ToString('yyyy-MM-ddTHH:mm:ss')
$forwardRepeat.EndBoundary = $forwardEnd.ToString('yyyy-MM-ddTHH:mm:ss')
$forwardPrincipal = New-ScheduledTaskPrincipal -UserId $forwardIdentity -LogonType Interactive -RunLevel Limited
$forwardAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $forwardActionArgs -WorkingDirectory $forwardRepo
$forwardSettings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$forwardTask = New-ScheduledTask -Action $forwardAction -Trigger @($forwardLogon, $forwardRepeat) -Principal $forwardPrincipal -Settings $forwardSettings -Description 'Isolated paper-only H04 forward collector restart guard. Keeps explicit stop, hashes and lease fencing; no Production app or model changes. Interactive user logon required.'
Register-ScheduledTask -TaskName $forwardTaskName -InputObject $forwardTask | Out-Null
$forwardPlan.installed = $true
$forwardPlan | ConvertTo-Json -Depth 5
