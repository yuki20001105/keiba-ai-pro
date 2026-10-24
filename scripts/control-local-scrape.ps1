param(
  [Parameter(Mandatory)][ValidateSet('status', 'cancel', 'start')][string]$Action,
  [string]$JobId,
  [string]$StartPeriod = '2016-01',
  [string]$EndPeriod = '2026-09'
)

$ErrorActionPreference = 'Stop'
$scrapeRepoRoot = Split-Path -Parent $PSScriptRoot
$scrapeSettings = @{}
foreach ($name in @('.env', '.env.local', '.env.test')) {
  $configPath = Join-Path $scrapeRepoRoot $name
  if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { continue }
  foreach ($rawLine in Get-Content -LiteralPath $configPath) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith('#')) { continue }
    $separator = $line.IndexOf('=')
    if ($separator -le 0) { continue }
    $value = $line.Substring($separator + 1).Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    $scrapeSettings[$line.Substring(0, $separator).Trim()] = $value
  }
}
$scrapeAuthOrigin = [Uri]$scrapeSettings['NEXT_PUBLIC_SUPABASE_URL']
if ($scrapeAuthOrigin.Scheme -ne 'https' -or -not $scrapeAuthOrigin.Host.EndsWith('.supabase.co')) {
  throw 'Configured authentication origin is not an approved Supabase HTTPS host.'
}
if (-not $scrapeSettings['NEXT_PUBLIC_SUPABASE_ANON_KEY'] -or
    -not $scrapeSettings['E2E_EMAIL'] -or -not $scrapeSettings['E2E_PASSWORD']) {
  throw 'Existing local-login configuration is incomplete.'
}
try {
  # Normal password authentication only. Never use service-role tokens,
  # fabricated owners, disabled guards, or log credentials/session tokens.
  $scrapeAuth = Invoke-RestMethod -Method Post -Uri "$($scrapeAuthOrigin.AbsoluteUri.TrimEnd('/'))/auth/v1/token?grant_type=password" `
    -Headers @{ apikey = $scrapeSettings['NEXT_PUBLIC_SUPABASE_ANON_KEY'] } -ContentType 'application/json' `
    -Body (@{ email = $scrapeSettings['E2E_EMAIL']; password = $scrapeSettings['E2E_PASSWORD'] } | ConvertTo-Json -Compress) -TimeoutSec 20
} catch { throw 'Normal local-account authentication failed; no acquisition mutation performed.' }
if (-not $scrapeAuth.access_token) { throw 'Authentication did not return a session; no acquisition mutation performed.' }
$scrapeHeaders = @{ Authorization = "Bearer $($scrapeAuth.access_token)" }
$scrapeApiOrigin = 'http://127.0.0.1:8000'

if ($Action -in @('status', 'cancel')) {
  if ($JobId -notmatch '^[0-9a-fA-F-]{36}$') { throw 'An explicit job UUID is required.' }
  # This endpoint verifies Admin AND current owner. A job belonging to
  # another account is not an authorized cancellation target.
  $scrapeStatus = Invoke-RestMethod -Method Get -Uri "$scrapeApiOrigin/api/scrape/status/$JobId" -Headers $scrapeHeaders -TimeoutSec 10
  if ($scrapeStatus.job_id -ne $JobId -or $scrapeStatus.status -eq 'not_found') {
    throw 'Owned job could not be verified; no mutation performed.'
  }
  if ($Action -eq 'cancel') {
    $scrapeStatus = Invoke-RestMethod -Method Post -Uri "$scrapeApiOrigin/api/scrape/cancel/$JobId" -Headers $scrapeHeaders -TimeoutSec 10
  }
  @{ job_id = $JobId; status = $scrapeStatus.status; request = $scrapeStatus.request_payload;
     progress = $scrapeStatus.progress; error = $scrapeStatus.error } | ConvertTo-Json -Depth 6
  exit 0
}

if ($StartPeriod -notmatch '^\d{4}-(0[1-9]|1[0-2])$' -or $EndPeriod -notmatch '^\d{4}-(0[1-9]|1[0-2])$') {
  throw 'Periods must be YYYY-MM.'
}
$scrapeStartDate = [DateTime]::ParseExact("$StartPeriod-01", 'yyyy-MM-dd', $null)
$scrapeEndDate = [DateTime]::ParseExact("$EndPeriod-01", 'yyyy-MM-dd', $null).AddMonths(1).AddDays(-1)
if ($scrapeStartDate -gt $scrapeEndDate) { throw 'Invalid period ordering.' }
$scrapeHealth = Invoke-RestMethod -Uri "$scrapeApiOrigin/health" -TimeoutSec 5
if ($scrapeHealth.scrape_capabilities.race_scopes -notcontains 'jra' -or
    $scrapeHealth.scrape_capabilities.jra_month_reuse -ne $true) { throw 'JRA-only capability not applied; no start request sent.' }
$scrapeNewId = [Guid]::NewGuid().ToString()
Write-Host "Reserved acquisition identity: $scrapeNewId (do not blindly resubmit after a timeout)"
$scrapePayload = @{
  start_date = $scrapeStartDate.ToString('yyyyMMdd'); end_date = $scrapeEndDate.ToString('yyyyMMdd');
  race_scope = 'jra'; server_batch = $true; force_rescrape = $false; dry_run = $false;
  job_id = $scrapeNewId; operation_id = [Guid]::NewGuid().ToString()
}
$scrapeStarted = Invoke-RestMethod -Method Post -Uri "$scrapeApiOrigin/api/scrape/start" -Headers $scrapeHeaders `
  -ContentType 'application/json' -Body ($scrapePayload | ConvertTo-Json -Compress) -TimeoutSec 15
if ($scrapeStarted.job_id -ne $scrapeNewId) { throw 'Start acknowledgement does not match the requested job; do not blindly resend.' }
$scrapeAccepted = Invoke-RestMethod -Uri "$scrapeApiOrigin/api/scrape/status/$scrapeNewId" -Headers $scrapeHeaders -TimeoutSec 10
if ($scrapeAccepted.request_payload.start_date -ne $scrapePayload.start_date -or
    $scrapeAccepted.request_payload.end_date -ne $scrapePayload.end_date -or
    $scrapeAccepted.request_payload.race_scope -ne 'jra') {
  throw "Accepted request differs from specified scope/period; inspect job $scrapeNewId."
}
@{ job_id = $scrapeNewId; status = $scrapeAccepted.status; request = $scrapeAccepted.request_payload;
   accepted_period_and_scope_verified = $true } | ConvertTo-Json -Depth 5
