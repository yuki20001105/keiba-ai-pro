@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run npm run setup:api from the repository root.
  exit /b 1
)

".venv\Scripts\python.exe" -X utf8 main.py
