@echo off
chcp 65001 >nul
cd /d C:\Users\yuki2\Documents\ws\keiba-ai-pro
set PYTHONPATH=C:\Users\yuki2\Documents\ws\keiba-ai-pro
C:\Users\yuki2\.pyenv\pyenv-win\versions\3.10.11\python.exe test_all.py
pause
