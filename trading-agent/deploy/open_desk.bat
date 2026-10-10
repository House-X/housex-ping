@echo off
REM Open the trading desk that runs on the server, through an encrypted SSH tunnel.
REM Keep this window open while you use the desk; close it when you are done.
cd /d "%~dp0"
set /p IP=<server_ip.txt
set IP=%IP: =%
title Trading Desk tunnel - keep open while using the desk
start "" cmd /c "timeout /t 4 >nul && start http://localhost:8501"
ssh -N -L 8501:127.0.0.1:8501 root@%IP%
