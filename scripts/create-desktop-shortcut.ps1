#!/usr/bin/env pwsh
# デスクトップショートカット作成スクリプト

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  デスクトップショートカット作成" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""

$ProjectRoot = $PSScriptRoot
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "競馬AI Pro.lnk"

# ショートカット作成
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$ProjectRoot\start-dev.ps1`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.IconLocation = "powershell.exe,0"
$Shortcut.Description = "競馬AI Pro開発環境を起動"
$Shortcut.Save()

Write-Host "ショートカット作成完了！" -ForegroundColor Green
Write-Host ""
Write-Host "デスクトップに以下が作成されました:" -ForegroundColor Cyan
Write-Host "  競馬AI Pro.lnk" -ForegroundColor White
Write-Host ""
Write-Host "ダブルクリックで起動できます！" -ForegroundColor Yellow
Write-Host ""
