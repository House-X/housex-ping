@echo off
REM Show the last 60 lines of the watcher log on the server.
cd /d "%~dp0"
set /p IP=<server_ip.txt
set IP=%IP: =%
ssh root@%IP% "journalctl -u trading-watcher -n 60 --no-pager"
pause
