param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $workspaceRoot "python-api\.venv\Scripts\python.exe"
$requirements = Join-Path $workspaceRoot "python-api\requirements.txt"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $resolvedPythonPath = $null
    if ($PythonExe) {
        $resolvedPython = Get-Command $PythonExe -ErrorAction SilentlyContinue
        if ($resolvedPython) {
            $resolvedPythonPath = $resolvedPython.Source
        }
    } elseif ($env:USERPROFILE) {
        $versionsRoot = Join-Path $env:USERPROFILE ".pyenv\pyenv-win\versions"
        $candidate = Get-ChildItem -LiteralPath $versionsRoot -Directory -Filter "3.11.*" -ErrorAction SilentlyContinue |
            Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "python.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($candidate) {
            $resolvedPythonPath = $candidate
        }
    }

    if (-not $resolvedPythonPath) {
        throw "Python 3.11 was not found. Pass -PythonExe with an approved Python 3.11 executable."
    }

    $version = & $resolvedPythonPath -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"
    if ($version -ne "3.11") {
        throw "Python 3.11 is required; resolved $resolvedPythonPath as Python $version."
    }
    & $resolvedPythonPath -m venv (Join-Path $workspaceRoot "python-api\.venv")
}

$venvVersion = & $venvPython -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"
if ($venvVersion -ne "3.11") {
    throw "The worktree venv must use Python 3.11; found Python $venvVersion."
}

& $venvPython -m pip install --disable-pip-version-check -r $requirements
& $venvPython -m pip install --disable-pip-version-check "pytest>=9,<10"
& $venvPython --version
