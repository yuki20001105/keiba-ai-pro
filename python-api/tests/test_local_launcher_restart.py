import json
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("entrypoint", "arguments", "restart", "development"),
    [
        ("start-keiba-ai-pro.bat", [], False, False),
        ("start-keiba-ai-pro.bat", ["-Restart"], True, False),
        ("scripts/start-all.bat", ["-Restart"], True, False),
        ("scripts/start-dev.bat", [], False, True),
        ("scripts/start-dev.ps1", [], False, True),
    ],
)
def test_entrypoints_share_launcher_without_forcing_restart(
    tmp_path, entrypoint, arguments, restart, development,
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "start-local-app.ps1").write_text(
        "param([switch]$Restart,[switch]$NoBrowser,[switch]$DevelopmentFrontend)\n"
        "@{restart=[bool]$Restart; noBrowser=[bool]$NoBrowser;"
        "development=[bool]$DevelopmentFrontend} | ConvertTo-Json -Compress\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target = tmp_path / entrypoint
    target.write_bytes((ROOT / entrypoint).read_bytes())
    # An obsolete dev:all entrypoint must fail without starting real services.
    (tmp_path / "npm.cmd").write_text("@exit /b 91\n", encoding="ascii")
    if target.suffix == ".ps1":
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "function npm { throw 'unguarded dev:all startup' }; & '"
            + str(target).replace("'", "''") + "' -NoBrowser",
            *arguments,
        ]
    else:
        command = (
            f'cmd.exe /d /s /c ""{target}" -NoBrowser {" ".join(arguments)}"'
        )
    result = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = next(
        (json.loads(line) for line in result.stdout.splitlines()
         if line.startswith("{")),
        None,
    )
    assert payload == {
        "restart": restart, "noBrowser": True, "development": development,
    }, result.stdout


def test_restart_recognizes_production_server_but_not_another_repository():
    script = r"""
$ErrorActionPreference='Stop'
$RepoRoot=$args[0]
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $RepoRoot 'scripts\start-local-app.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Launcher syntax invalid'}
$fn=$ast.Find({param($a)
    $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $a.Name -eq 'Find-RepositoryWebProcess'
},$true)
Invoke-Expression $fn.Extent.Text
function Get-NetTCPConnection { [pscustomobject]@{OwningProcess=12345} }
function Get-CimInstance { $script:Fixture }
function Get-Process { param($Id) [pscustomobject]@{Id=$Id} }
$node='C:\tools\node.exe'
$script:Fixture=[pscustomobject]@{
    ExecutablePath=$node
    CommandLine=('node "'+(Join-Path $RepoRoot '.next\standalone\server.js')+'"')
    ParentProcessId=0
}
if(-not(Find-RepositoryWebProcess $node)){throw 'Production server not recognized'}
$script:Fixture.CommandLine='node "C:\other\.next\standalone\server.js"'
if(Find-RepositoryWebProcess $node){throw 'Unrelated server admitted'}
$script:Fixture.CommandLine=('node "'+(Join-Path $RepoRoot 'node_modules\next\dist\server\lib\start-server.js')+'"')
if(-not(Find-RepositoryWebProcess $node)){throw 'Development server no longer recognized'}
$script:Fixture.CommandLine=('node "'+(Join-Path $RepoRoot 'node_modules\next\dist\bin\next')+'" start --hostname 127.0.0.1')
if(-not(Find-RepositoryWebProcess $node)){throw 'Local production CLI not recognized'}
$script:Fixture.CommandLine='node "C:\other\node_modules\next\dist\bin\next" start'
if(Find-RepositoryWebProcess $node){throw 'Unrelated production CLI admitted'}
$script:Fixture.CommandLine=$null
if(Find-RepositoryWebProcess $node){throw 'Uninspectable server admitted'}
"""
    # Pass a literal repository path; no shell expansion or credentials.
    command = "& { " + script + " } '" + str(ROOT).replace("'", "''") + "'"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_local_production_build_uses_webpack_not_turbopack():
    command = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PWD 'scripts\start-local-app.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Launcher syntax invalid'}
$assignment=$ast.Find({param($a)
    $a -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $a.Left.Extent.Text -eq '$BuildProcess'
},$true)
$RepoRoot=$PWD.Path
$WebStdout='build.stdout.log'; $WebStderr='build.stderr.log'
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,
        $RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru,[switch]$Wait)
    if($FilePath -ne 'cmd.exe' -or
       $ArgumentList[-1] -ne 'npm run build -- --webpack' -or
       -not $Wait){throw 'Local build must use synchronous Webpack compilation'}
    [pscustomobject]@{ExitCode=0}
}
Invoke-Expression $assignment.Extent.Text
if($BuildProcess.ExitCode -ne 0){throw 'Build exit code lost'}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_second_launcher_exits_before_reading_configuration():
    owner = subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$m=New-Object System.Threading.Mutex($false,"
            "'Local\\KeibaAIProLocalLauncher'); "
            "if(-not $m.WaitOne(0)){exit 2}; "
            "[Console]::WriteLine('locked'); [Console]::Out.Flush(); "
            "$null=[Console]::In.ReadLine(); $m.ReleaseMutex()",
        ],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout.readline().strip() == "locked"
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File",
                str(ROOT / "scripts" / "start-local-app.ps1"),
                "-ConfigurationRoot", str(ROOT / "does-not-exist"),
                "-Restart", "-NoBrowser",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "startup is already in progress" in result.stdout
    finally:
        owner.communicate("\n", timeout=10)
