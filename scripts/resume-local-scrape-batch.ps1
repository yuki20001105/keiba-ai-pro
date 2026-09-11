param(
  [string]$StartPeriod = '2018-11',
  [string]$EndPeriod = '2026-08',
  [switch]$Repair,
  [string]$TailRepairPeriod = '2018-10',
  [string]$AppUrl = 'http://127.0.0.1:3000'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$configRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '..\keiba-ai-pro'))
$logRoot = Join-Path $repoRoot 'logs\local-app'
$statePath = Join-Path $logRoot 'scrape-resume-state.json'
$logPath = Join-Path $logRoot 'scrape-resume.log'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-RunnerLog([string]$Message) {
  $line = '{0} {1}' -f (Get-Date).ToString('o'), $Message
  Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Read-DotEnv([string]$Path, [hashtable]$Target) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  foreach ($rawLine in Get-Content -LiteralPath $Path) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith('#')) { continue }
    $separator = $line.IndexOf('=')
    if ($separator -le 0) { continue }
    $name = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1).Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    $Target[$name] = $value
  }
}

function Convert-PeriodToRange([string]$Period) {
  if ($Period -notmatch '^(\d{4})-(0[1-9]|1[0-2])$') {
    throw "Invalid period: $Period"
  }
  $year = [int]$Matches[1]
  $month = [int]$Matches[2]
  $lastDay = [DateTime]::DaysInMonth($year, $month)
  return [pscustomobject]@{
    Period = $Period
    StartDate = '{0:D4}{1:D2}01' -f $year, $month
    EndDate = '{0:D4}{1:D2}{2:D2}' -f $year, $month, $lastDay
  }
}

function New-PeriodPlan([string]$Start, [string]$End, [string]$Tail) {
  $startDate = [DateTime]::ParseExact("$Start-01", 'yyyy-MM-dd', $null)
  $endDate = [DateTime]::ParseExact("$End-01", 'yyyy-MM-dd', $null)
  if ($startDate -gt $endDate) { throw 'StartPeriod must be before EndPeriod' }
  $plan = New-Object System.Collections.Generic.List[object]
  for ($cursor = $startDate; $cursor -le $endDate; $cursor = $cursor.AddMonths(1)) {
    $plan.Add((Convert-PeriodToRange $cursor.ToString('yyyy-MM')))
  }
  if ($Tail) { $plan.Add((Convert-PeriodToRange $Tail)) }
  return $plan
}

$settings = @{}
Read-DotEnv (Join-Path $configRoot '.env') $settings
Read-DotEnv (Join-Path $configRoot '.env.local') $settings
Read-DotEnv (Join-Path $configRoot '.env.test') $settings

$supabaseUrl = $settings['NEXT_PUBLIC_SUPABASE_URL']
$supabaseAnonKey = $settings['NEXT_PUBLIC_SUPABASE_ANON_KEY']
$email = $settings['E2E_EMAIL']
$password = $settings['E2E_PASSWORD']
if (-not $supabaseUrl -or -not $supabaseAnonKey -or -not $email -or -not $password) {
  throw 'Local authentication configuration is incomplete'
}

$script:accessToken = $null
$script:tokenIssuedAt = [DateTime]::MinValue

function Update-AccessToken {
  $authBody = @{ email = $email; password = $password } | ConvertTo-Json -Compress
  $auth = Invoke-RestMethod -Method Post -Uri "$supabaseUrl/auth/v1/token?grant_type=password" `
    -Headers @{ apikey = $supabaseAnonKey } -ContentType 'application/json' -Body $authBody -TimeoutSec 30
  if (-not $auth.access_token) { throw 'Authentication did not return an access token' }
  $script:accessToken = [string]$auth.access_token
  $script:tokenIssuedAt = Get-Date
}

function Get-ApiHeaders {
  if (-not $script:accessToken -or ((Get-Date) - $script:tokenIssuedAt).TotalMinutes -ge 45) {
    Update-AccessToken
  }
  return @{ Authorization = "Bearer $script:accessToken" }
}

function Save-State([hashtable]$State) {
  $State['updated_at'] = (Get-Date).ToString('o')
  $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding UTF8
}

function Wait-ScrapeJob([string]$JobId, [string]$Period) {
  $deadline = (Get-Date).AddHours(24)
  $transportFailures = 0
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 10
    try {
      $status = Invoke-RestMethod -Method Get -Uri "$AppUrl/api/scrape/status/$JobId" `
        -Headers (Get-ApiHeaders) -TimeoutSec 30
      $transportFailures = 0
    } catch {
      $transportFailures += 1
      if ($transportFailures -ge 10) { throw "Status monitoring failed for $Period ($JobId)" }
      continue
    }

    if ($status.status -eq 'completed') {
      $races = if ($null -ne $status.result.races_collected) { [int]$status.result.races_collected } else { 0 }
      $horses = if ($null -ne $status.result.saved_horses) { [int]$status.result.saved_horses } else { 0 }
      $quality = if ($null -ne $status.result.success) { [bool]$status.result.success } else { $false }
      Write-RunnerLog "completed period=$Period job_id=$JobId races=$races horses=$horses quality_complete=$quality"
      return
    }
    if ($status.status -eq 'error' -or $status.status -eq 'not_found') {
      throw "Scrape job ended with status=$($status.status) for $Period ($JobId)"
    }
  }
  throw "24-hour monitoring deadline reached for $Period ($JobId)"
}

$plan = @(New-PeriodPlan $StartPeriod $EndPeriod $TailRepairPeriod)
$planId = "$StartPeriod..$EndPeriod|repair=$($Repair.IsPresent)|tail=$TailRepairPeriod"
$nextIndex = 0
$resumeJobId = $null

if (Test-Path -LiteralPath $statePath) {
  try {
    $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($saved.plan_id -eq $planId -and $saved.status -ne 'completed') {
      $nextIndex = [int]$saved.next_index
      if ($saved.status -eq 'running') { $resumeJobId = [string]$saved.job_id }
    }
  } catch {
    Write-RunnerLog 'checkpoint_invalid; starting from configured first period'
  }
}

Write-RunnerLog "runner_started plan=$planId count=$($plan.Count) next_index=$nextIndex"

for ($index = $nextIndex; $index -lt $plan.Count; $index += 1) {
  $range = $plan[$index]
  $jobId = $null
  if ($resumeJobId -and $index -eq $nextIndex) {
    $jobId = $resumeJobId
    Write-RunnerLog "resuming period=$($range.Period) job_id=$jobId"
  } else {
    $body = @{
      start_date = $range.StartDate
      end_date = $range.EndDate
      force_rescrape = $Repair.IsPresent
      dry_run = $false
    } | ConvertTo-Json -Compress
    $started = Invoke-RestMethod -Method Post -Uri "$AppUrl/api/scrape" `
      -Headers (Get-ApiHeaders) -ContentType 'application/json' -Body $body -TimeoutSec 30
    $jobId = [string]$started.job_id
    if (-not $jobId) { throw "Start response did not include job_id for $($range.Period)" }
    Write-RunnerLog "started period=$($range.Period) job_id=$jobId"
  }

  Save-State @{
    plan_id = $planId
    status = 'running'
    next_index = $index
    current_period = $range.Period
    job_id = $jobId
  }
  Wait-ScrapeJob $jobId $range.Period
  $resumeJobId = $null
  Save-State @{
    plan_id = $planId
    status = 'between_months'
    next_index = $index + 1
    current_period = $range.Period
    job_id = $null
  }
}

Save-State @{
  plan_id = $planId
  status = 'completed'
  next_index = $plan.Count
  current_period = $null
  job_id = $null
}
Write-RunnerLog "runner_completed plan=$planId"
