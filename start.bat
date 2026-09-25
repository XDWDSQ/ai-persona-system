@echo off
chcp 65001 >nul
REM ============================================================
REM  AI 拟人系统 一键启动
REM  解释器由 _find_python.bat 统一探测（PYTHON 环境变量 -> 项目
REM  venv -> PATH）。第九轮修正：此前硬编码
REM  %USERPROFILE%\.openvino\venv\t2i-tts，换机后本文件直接判"环境未就绪"，
REM  而项目自带的 venv 就躺在旁边没人用。
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

REM 保持 TTS/ASR 常驻，避免闲置后重新加载模型
set "INTEL_SKILL_DOG_NO_EVICTION=1"

REM 已在跑就直接开浏览器：不加这道判断时，双击第二次会再起一个 uvicorn，
REM 新进程必然因 8000 被占用而崩在 bind 上，窗口里只剩一屏看不懂的 traceback
"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  服务已在运行，直接打开 http://127.0.0.1:8000
    echo  需要加载最新代码请改跑 restart_service.bat
    echo.
    start "" http://127.0.0.1:8000
    pause
    exit /b 0
)

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