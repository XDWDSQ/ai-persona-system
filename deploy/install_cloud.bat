@echo off
REM ============================================================
REM  AI Persona System - one-click install & start on cloud PC
REM  Run inside the extracted cloud_deploy folder.
REM  Steps: 1) install deps  2) start uvicorn (detached)
REM         3) verify health  4) hint about phone access
REM ============================================================
cd /d "%~dp0"

echo.
echo  [1/3] Checking Python ...
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] Python not found. Install Python 3.11 first:
    echo      https://www.python.org/downloads/
    echo      Remember to check "Add python.exe to PATH".
    pause
    exit /b 1
)
python --version

echo.
echo  [2/3] Installing dependencies (fastapi uvicorn httpx pydantic multipart) ...
python -m pip install -r requirements.txt

echo.
echo  [3/3] Starting service on port 8000 (background, log: data\service.log) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath 'python' -ArgumentList '-m','uvicorn','server:app','--host','0.0.0.0','--port','8000','--timeout-graceful-shutdown','10' -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput 'data\service.log' -RedirectStandardError 'data\service_err.log' -PassThru; Start-Sleep -Seconds 3; Write-Host ('Service PID: ' + $p.Id)"

echo.
echo  Verifying health ...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 8; Write-Host ('Health: ' + $r.Content) } catch { Write-Host ('[!] Health check failed: ' + $_.Exception.Message) }"

echo.
echo  ============================================================
echo   Local test:  http://127.0.0.1:8000   (token: see config.json)
echo   Phone access: run start_tunnel.bat to open a Cloudflare
echo   tunnel, then open the shown https://xxx.trycloudflare.com
echo   on your phone and enter the access token.
echo  ============================================================
echo.
pause
