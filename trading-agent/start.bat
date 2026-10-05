@echo off
REM One-click start: browser interface + position watcher (Windows)
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo Virtual environment not found. Run the setup steps in README first.
  pause
  exit /b 1
)
start "Trading Watcher - keep open" cmd /k ".venv\Scripts\activate.bat && python main.py --watch"
call .venv\Scripts\activate.bat
title Trading Desk - keep open
streamlit run app.py
pause
