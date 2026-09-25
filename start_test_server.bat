@echo off
setlocal
chcp 65001 >nul
REM ============================================================
REM  测试专用隔离后端实例（端口 8010 + data-test/ 数据目录）
REM  真实服务（8000 + ngrok）不受影响，无需事后清理真实数据。
REM  解释器由 _find_python.bat 统一探测（第九轮修正）。
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

REM 数据目录指向 data-test/（server.py 支持 AI_DATA_DIR 覆盖）
set "AI_DATA_DIR=%CD%\data-test"

echo.
echo  AI 拟人系统 [测试实例]：http://127.0.0.1:8010
echo  数据目录：%AI_DATA_DIR%  （与真实服务 data/ 完全隔离）
echo  关闭本窗口即停止测试实例。
echo.
"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8010 --timeout-graceful-shutdown 10
pause