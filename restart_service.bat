@echo off
chcp 65001 >nul
REM ============================================================
REM  重启 AI 拟人系统 8000 端口服务（加载最新代码）
REM  双击运行即可：自动停掉旧服务 -> 启动新服务
REM  解释器由 _find_python.bat 统一探测（第九轮修正：此前硬编码
REM  %USERPROFILE%\.openvino\venv\t2i-tts，换机后直接报"未找到运行环境"）。
REM ============================================================
cd /d "%~dp0"

call "%~dp0_find_python.bat" uvicorn
if not defined PYTHON (
    echo [X] 未找到可用的后端解释器
    echo     %PYTHON_ERR%
    echo     请先运行 setup.bat 完成初始化，或设置 PYTHON=<python.exe 路径>
    pause
    exit /b 1
)
set "PY=%PYTHON%"

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
"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10
pause