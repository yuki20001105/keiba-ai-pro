@echo off
echo =====================================
echo Starting Keiba AI Services
echo =====================================
echo.

REM FastAPIサーバーを起動（バックグラウンド）
echo Starting FastAPI Server...
start "FastAPI Server" cmd /k "cd python-api && .venv\Scripts\python.exe main.py"

REM 少し待機
timeout /t 3 /nobreak > nul

REM Next.jsサーバーを起動（バックグラウンド）
echo Starting Next.js Server...
start "Next.js Server" cmd /k "npm run dev"

echo.
echo =====================================
echo Both servers are starting!
echo FastAPI: http://localhost:8000
echo Next.js: http://localhost:3000
echo =====================================
echo.
echo Press any key to exit (servers will keep running)...
pause > nul
