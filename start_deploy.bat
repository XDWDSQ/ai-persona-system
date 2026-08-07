@echo off
REM ============================================================
REM  AI 拟人系统 - 部署机一键自启（uvicorn + ngrok 固定域名）
REM  用途：电脑开机/登录后自动恢复部署机服务，手机无需改地址
REM  放在「启动」文件夹即可实现登录自启：
REM    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
REM  已在运行的服务会跳过，不会重复启动。
REM ============================================================
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
set "NGROK=%LOCALAPPDATA%\ngrok\ngrok.exe"

REM 1) 后端服务：已在线则跳过
"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >nul 2>&1
if errorlevel 1 (
    echo [start] 启动后端服务 :8000 ...
    start "" /min "%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10
) else (
    echo [start] 后端已在运行，跳过
)

REM 2) ngrok 固定域名隧道：已在运行则跳过
tasklist | findstr /i "ngrok.exe" >nul 2>&1
if errorlevel 1 (
    echo [start] 启动 ngrok 隧道 ...
    start "" /min "%NGROK%" http 8000 --url https://filling-smirk-sternness.ngrok-free.dev
) else (
    echo [start] ngrok 已在运行，跳过
)

echo [start] 部署机自启完成。手机访问 https://filling-smirk-sternness.ngrok-free.dev
