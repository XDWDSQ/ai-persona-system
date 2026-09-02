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

REM 本地 LLM 已从界面下线（云端 MiniMax 为主引擎）；如需恢复本地推理，
REM 手动运行 llm\start_llm.bat 并在设置里把引擎切回本地。

REM 轻量校验后端依赖（安装已迁移到 setup.bat）
"%PY%" -c "import fastapi, uvicorn, httpx, pydantic" || (echo 依赖缺失，请先运行 setup.bat & exit /b 1)

echo.
echo  AI 拟人系统已启动：http://127.0.0.1:8000
echo  远程访问：先启动 ngrok（ngrok http 8000），地址见 data\tunnel_url.txt
echo  公网访问前必须配置访问口令（config.json 顶层 access_token）。
echo  关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8000
"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10
pause
