@echo off
REM ============================================================
REM  AI Persona System - Cloudflare Tunnel launcher (phone access)
REM  Usage: 1) run start.bat first to bring up the local service
REM         2) double-click this script
REM  Note: quick tunnel URL changes on every start. The new URL is
REM        auto-saved to data\tunnel_url.txt and shown in this window.
REM ============================================================
cd /d "%~dp0"

set "CFD=C:\Program Files (x86)\cloudflared\cloudflared.exe"

if not exist "%CFD%" (
    echo [X] cloudflared not found. Install it with:
    echo     winget install --id Cloudflare.cloudflared -e
    pause
    exit /b 1
)

echo.
echo  AI Persona System - Cloudflare Tunnel starting ...
echo  New URL will be saved to data\tunnel_url.txt
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tunnel.ps1"

pause
