@echo off
REM AI 拟人系统 一键启动
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

if not exist "%PY%" (
    echo [X] 未找到 TTS 运行环境，请先运行 setup.bat 完成初始化
    pause
    exit /b 1
)

REM 保持 TTS/ASR 常驻，避免闲置后重新加载模型
set "INTEL_SKILL_DOG_NO_EVICTION=1"

REM 启动本地 LLM（本地模式用；若已配置云端 API 可跳过，不影响）
call "llm\start_llm.bat"

REM 确保后端依赖
"%PY%" -m pip install -r requirements.txt -q 2>nul

echo.
echo  AI 拟人系统已启动：http://127.0.0.1:8000
echo  关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8000
"%PY%" -m uvicorn server:app --host 127.0.0.1 --port 8000
pause
