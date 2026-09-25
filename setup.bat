@echo off
chcp 65001 >nul
REM ============================================================
REM  AI 拟人系统 首次初始化 / 环境修复（一次性）
REM  作用：1) 准备/校验后端解释器  2) 安装后端依赖  3) 可选本地 TTS 环境
REM  说明：本地 LLM 已从界面下线（云端 API 为主引擎），不再预下载模型；
REM        如需本地推理，手动运行 llm\get_llm.py 与 llm\start_llm.bat。
REM
REM  第九轮重写：此前本脚本只认 %USERPROFILE%\.openvino\venv\t2i-tts，
REM  该路径不存在时直接打印"环境未就绪"然后退出 —— 一台新机器永远
REM  初始化不起来。现在改成：优先复用已有解释器，否则自动建项目 venv。
REM ============================================================
cd /d "%~dp0"

echo === [1/3] 准备后端 Python 解释器 ===
REM 先看有没有现成能用的（PYTHON 环境变量 / 项目 venv / PATH）
call "%~dp0_find_python.bat" uvicorn
if defined PYTHON (
    echo [OK] 复用已有解释器：%PYTHON%
    set "PY=%PYTHON%"
    goto :deps
)

echo [..] 未找到可用解释器，尝试创建项目虚拟环境 venv\
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv venv
) else (
    python -m venv venv
)
if not exist "venv\Scripts\python.exe" (
    echo [错误] 创建 venv 失败。请安装 Python 3.11+ 并勾选 "Add to PATH"，然后重跑本脚本。
    pause
    exit /b 1
)
set "PY=%CD%\venv\Scripts\python.exe"
echo [OK] 已创建：%PY%

:deps
echo === [2/3] 安装后端依赖 ===
"%PY%" -m pip install --upgrade pip -q
"%PY%" -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络（国内可加 -i https://pypi.tuna.tsinghua.edu.cn/simple）
    pause
    exit /b 1
)

echo === [3/3] 可选：本地 TTS 声音克隆环境（云端合成不需要） ===
if exist "%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1' -SkillRoot '%USERPROFILE%\.trae-cn\skills\local-tts'"
) else (
    echo [注意] 本地 TTS skill 未安装（云端合成不受影响，可跳过）
)

echo.
echo 初始化完成！运行 start.bat 启动系统（http://127.0.0.1:8000）。
pause