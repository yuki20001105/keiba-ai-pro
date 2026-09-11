@echo off
setlocal
chcp 65001 >nul
title Keiba AI Pro Launcher
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local-app.ps1" -DevelopmentFrontend
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Startup failed. Review the message above and logs\local-app.
  pause
)
exit /b %EXIT_CODE%
