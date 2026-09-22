@echo off
chcp 65001 >nul
REM ============================================================
REM  测试专用隔离后端实例
REM  数据目录独立（data-test/）+ 端口 8010，测试数据绝不污染真实服务：
REM    - 模拟器测试：App 设置页服务器地址填  http://10.0.2.2:8010
REM    - 脚本测试：  set VERIFY_BASE=http://127.0.0.1:8010 再跑验证脚本
REM  真实服务（8000 + ngrok）不受任何影响，无需事后清理真实数据。
REM  测完可选跑 cleanup_test_sessions.py 清理本实例里的测试会话。
REM ============================================================
cd /d "%~dp0"

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
if not exist "%PY%" (
    echo [X] 未找到 TTS 运行环境，请先运行 setup.bat 完成初始化
    pause
    exit /b 1
)

REM 数据目录指向 data-test/（server.py 支持 AI_DATA_DIR 覆盖）
set "AI_DATA_DIR=%CD%\data-test"

echo.
echo  AI 拟人系统 [测试实例] 已启动：http://127.0.0.1:8010
echo  数据目录：%AI_DATA_DIR%  （与真实服务 data/ 完全隔离）
echo.
echo  模拟器内访问：http://10.0.2.2:8010 （App 设置页改服务器地址）
echo  关闭本窗口即停止测试实例。
echo.
"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8010 --timeout-graceful-shutdown 10
pause
