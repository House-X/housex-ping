@echo off
REM Send the agent to the server.
REM   upload.bat first   -> code + .env + data (your account state). Use ONCE, for the move.
REM   upload.bat         -> code only (updates). Your .env and data on the server are kept.
cd /d "%~dp0\.."
if not exist "deploy\server_ip.txt" (
  set /p IP="Server IP address: "
  call echo %%IP%%> deploy\server_ip.txt
)
set /p IP=<deploy\server_ip.txt
set IP=%IP: =%

if /i "%1"=="first" (
  echo Packing code, .env and data...
  tar -czf "%TEMP%\agent.tgz" --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=deploy/server_ip.txt .
) else (
  echo Packing code only...
  tar -czf "%TEMP%\agent.tgz" --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=deploy/server_ip.txt --exclude=.env --exclude=data .
)
if errorlevel 1 goto :fail
echo Uploading to %IP% ...
scp "%TEMP%\agent.tgz" root@%IP%:/tmp/agent.tgz
if errorlevel 1 goto :fail
ssh root@%IP% "mkdir -p /opt/trading-agent && tar -xzf /tmp/agent.tgz -C /opt/trading-agent && rm /tmp/agent.tgz && sed -i 's/\r$//' /opt/trading-agent/deploy/*.sh /opt/trading-agent/deploy/*.service && bash /opt/trading-agent/deploy/server_setup.sh"
if errorlevel 1 goto :fail
del "%TEMP%\agent.tgz"
echo.
echo ===== Done. The agent runs on the server. =====
pause
exit /b 0
:fail
echo.
echo ===== Something failed - send a screenshot of this window. =====
pause
exit /b 1
