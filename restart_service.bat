@echo off
REM ============================================================
REM  重启 AI 拟人系统 8000 端口服务（加载最新代码）
REM  双击运行即可：自动停掉旧服务 -> 启动新服务
REM ============================================================
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

if not exist "%PY%" (
    echo [X] 未找到 TTS 运行环境：%PY%
    pause
    exit /b 1
)

REM 找到占用 8000 的旧进程并结束
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    echo [*] 正在停止旧服务 PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

REM 保持 TTS/ASR 常驻，避免闲置后重新加载模型
set "INTEL_SKILL_DOG_NO_EVICTION=1"

echo.
echo  AI 拟人系统重启中：http://127.0.0.1:8000
echo  关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8000
"%PY%" -m uvicorn server:app --host 127.0.0.1 --port 8000
pause
