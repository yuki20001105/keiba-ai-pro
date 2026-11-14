[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$TailscalePath
)

$ErrorActionPreference = 'Stop'
$FrontendHealthUrl = 'http://127.0.0.1:3000/api/health'
$FrontendProxyUrl = 'http://127.0.0.1:3000'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This setup script is for the Windows PC that runs Keiba AI Pro.'
}
if (-not $TailscalePath) {
    $TailscalePath = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
}
if (-not (Test-Path -LiteralPath $TailscalePath -PathType Leaf)) {
    throw "Tailscale CLI was not found: $TailscalePath"
}

function Invoke-Tailscale([string[]]$Arguments, [int]$TimeoutSeconds = 15) {
    $stdoutPath = [IO.Path]::GetTempFileName()
    $stderrPath = [IO.Path]::GetTempFileName()
    $process = $null
    try {
        $process = Start-Process -FilePath $TailscalePath -ArgumentList $Arguments `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
            $output = @(
                Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
                Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
            ) -join "`n"
            throw "Tailscale CLI timed out after $TimeoutSeconds seconds.`n$output"
        }

        $output = @(
            Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction SilentlyContinue
            Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        ) -join "`n"
        if ($process.ExitCode -ne 0) {
            throw "Tailscale command failed (exit $($process.ExitCode)): tailscale $($Arguments -join ' ')`n$output"
        }
        return [pscustomobject]@{ Output = $output; ExitCode = $process.ExitCode }
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

try {
    $health = Invoke-WebRequest -Uri $FrontendHealthUrl -UseBasicParsing -TimeoutSec 5
}
catch {
    throw "Keiba AI Pro is not healthy at $FrontendHealthUrl. Start it with start-keiba-ai-pro.bat first. $($_.Exception.Message)"
}
if ($health.StatusCode -lt 200 -or $health.StatusCode -ge 300) {
    throw "Keiba AI Pro health check returned HTTP $($health.StatusCode)."
}

$tailnetStatusResult = Invoke-Tailscale -Arguments @('status', '--json')
try {
    $tailnetStatus = $tailnetStatusResult.Output | ConvertFrom-Json
}
catch {
    throw "Could not read Tailscale status JSON. $($_.Exception.Message)"
}
if ($tailnetStatus.BackendState -ne 'Running' -or $tailnetStatus.Self.Online -ne $true) {
    throw 'Tailscale is not connected. Sign in to Tailscale on this PC and try again.'
}

$dnsName = ([string]$tailnetStatus.Self.DNSName).TrimEnd('.')
if ($dnsName -notmatch '^[a-z0-9.-]+\.ts\.net$') {
    throw "Tailscale did not report a valid HTTPS hostname: $dnsName"
}

$existingConfig = (Invoke-Tailscale -Arguments @('serve', 'status', '--json')).Output.Trim()
if ($existingConfig -and $existingConfig -notmatch '^(?:\{\}|null)$') {
    throw "An existing Tailscale Serve configuration was found. Nothing was changed. Review it with 'tailscale serve status' before setting up this route.`n$existingConfig"
}

if ($PSCmdlet.ShouldProcess("https://$dnsName", "Privately proxy to $FrontendProxyUrl")) {
    $null = Invoke-Tailscale -Arguments @('serve', '--bg', '--yes', '--https=443', $FrontendProxyUrl) -TimeoutSeconds 30
    $configuredStatus = Invoke-Tailscale -Arguments @('serve', 'status', '--json')
    if ($configuredStatus.Output -notmatch [regex]::Escape($FrontendProxyUrl)) {
        throw "Tailscale Serve returned successfully, but the route could not be verified. Inspect it with 'tailscale serve status'.`n$($configuredStatus.Output)"
    }
    Write-Host "Private smartphone access is configured: https://$dnsName/"
    Write-Host 'Open this URL on a smartphone connected to the same Tailscale network, then sign in normally.'
}
else {
    Write-Host "No route was changed. The intended private URL is https://$dnsName/"
}
