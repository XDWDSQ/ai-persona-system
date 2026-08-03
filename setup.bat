@echo off
REM AI 拟人系统 首次初始化（一次性）
REM 作用：创建 TTS/ASR 运行环境、下载本地 LLM 模型、安装后端依赖
cd /d "%~dp0"
set "CURL=C:\Windows\System32\curl.exe"
set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"

echo === [1/5] 准备 TTS 声音克隆环境（Qwen3-TTS）===
if exist "%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%USERPROFILE%\.trae-cn\skills\local-tts\scripts\install-env.ps1' -SkillRoot '%USERPROFILE%\.trae-cn\skills\local-tts'"
) else (
    echo [错误] TTS skill 尚未安装，请先从 OpenClaw 安装 local-tts skill
)

echo === [2/5] 准备 ASR 语音识别环境（Qwen3-ASR）===
if exist "%USERPROFILE%\.trae-cn\skills\local-asr\scripts\install-env.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%USERPROFILE%\.trae-cn\skills\local-asr\scripts\install-env.ps1' -SkillRoot '%USERPROFILE%\.trae-cn\skills\local-asr'"
) else (
    echo [错误] ASR skill 尚未安装，请先从 OpenClaw 安装 local-asr skill
)

echo === [3/5] 从上游获取本地 LLM（llama.cpp 运行时 + Qwen3-4B 模型）===
if exist "%PY%" (
    "%PY%" "llm\get_llm.py"
) else (
    echo [注意] Python 环境未就绪，跳过模型获取，稍后可在 start.bat 时自动获取
)
if exist "%PY%" (
    "%PY%" -m pip install -r requirements.txt -q
) else (
    echo [注意] TTS 环境未生成，请检查 [1/5] 步骤
)

echo.
echo 初始化完成！运行 start.bat 启动系统。
pause
