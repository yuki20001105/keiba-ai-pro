@echo off
chcp 65001 >nul
echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo   競馬AI Pro 開発環境起動
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo 起動するサービス:
echo   - Next.js開発サーバー (http://localhost:3000)
echo   - Python APIサーバー (http://localhost:8001)
echo.
echo 起動中...
echo.

cd /d %~dp0

REM PATHを最新状態に更新（npmコマンドが見つからない問題を回避）
for /f "tokens=2*" %%a in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path') do set "MachinePath=%%b"
for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path') do set "UserPath=%%b"
set "Path=%MachinePath%;%UserPath%"

npm run dev:all
