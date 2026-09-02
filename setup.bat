@echo off
REM AI 拟人系统 首次初始化（一次性）
REM 作用：创建 TTS 运行环境、安装后端依赖
REM 说明：本地 LLM 已从界面下线（云端 API 为主引擎），不再预下载模型；
REM       如需本地推理，手动运行 llm\get_llm.py 与 llm\start_llm.bat。
cd /d "%~dp0"
set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

echo === [1/3] 准备 TTS 声音克隆环境（Qwen3-TTS）===
if exist "%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1' -SkillRoot '%USERPROFILE%\.trae-cn\skills\local-tts'"
) else (
    echo [注意] 本地 TTS skill 未安装（云端合成不受影响，可跳过）
)

echo === [2/3] 安装后端依赖 ===
if exist "%PY%" (
    "%PY%" -m pip install -r requirements.txt -q
) else (
    echo [错误] Python 环境未就绪，请先按 README 准备 %PY%
)

echo === [3/3] 完成 ===
echo 初始化完成！运行 start.bat 启动系统。
pause
