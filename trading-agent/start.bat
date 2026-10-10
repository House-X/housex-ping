@echo off
REM One-click start: browser interface + position watcher (Windows)
cd /d "%~dp0"
if exist "deploy\deployed.txt" (
  REM The agent lives on the server now: open the desk there instead of starting a 2nd watcher.
  call deploy\open_desk.bat
  exit /b 0
)
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
