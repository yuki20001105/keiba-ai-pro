[CmdletBinding()]
param(
    [string]$TailscaleExe = (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe')
)

$ErrorActionPreference = 'Stop'
$FrontendUrl = 'http://127.0.0.1:3000'

if (-not (Test-Path -LiteralPath $TailscaleExe -PathType Leaf)) {
    throw "Tailscale CLI was not found: $TailscaleExe"
}

try {
    Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
}
catch {
    throw "The local frontend is not responding at $FrontendUrl. Start Keiba AI Pro first."
}

& $TailscaleExe serve --bg --yes --https=443 $FrontendUrl
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale Serve failed with exit code $LASTEXITCODE."
}
