@echo off
chcp 65001 >nul
REM ============================================================
REM  AI 拟人系统 - 部署机一键自启（uvicorn + ngrok 固定域名）
REM  用途：电脑开机/登录后自动恢复部署机服务，手机无需改地址
REM  放在「启动」文件夹即可实现登录自启：
REM    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
REM  已在运行的服务会跳过，不会重复启动。
REM
REM  可用环境变量覆盖（任务计划/不同机器部署时）：
REM    AI_PERSONA_PY     后端解释器路径（优先级最高）
REM    AI_PERSONA_NGROK  ngrok.exe 路径
REM    AI_PERSONA_URL    ngrok 固定域名（留空则不带 --url 起隧道）
REM  未指定 AI_PERSONA_PY 时由 _find_python.bat 统一探测（第九轮修正：
REM  此前硬编码 %USERPROFILE%\.openvino\venv\t2i-tts，换机即静默失败）。
REM ============================================================
cd /d "%~dp0"

set "PY="
set "PYTHON="
if not "%AI_PERSONA_PY%"=="" set "PY=%AI_PERSONA_PY%"
if not defined PY call "%~dp0_find_python.bat" uvicorn
if defined PYTHON set "PY=%PYTHON%"

if not "%AI_PERSONA_NGROK%"=="" set "NGROK=%AI_PERSONA_NGROK%"
if not defined NGROK set "NGROK=%LOCALAPPDATA%\ngrok\ngrok.exe"
if not defined AI_PERSONA_URL set "AI_PERSONA_URL=https://filling-smirk-sternness.ngrok-free.dev"

REM 解释器缺失必须直接退出：此前只用 errorlevel 判「服务是否在跑」，
REM python 不存在时 errorlevel=9009 同样 >=1，会被误判成「没在运行」，
REM 于是拿不存在的解释器去 start，最后还打印"自启完成" —— 任务计划静默失败。
if not defined PY (
    echo [X] 未找到后端解释器：%PYTHON_ERR%
    echo     用 AI_PERSONA_PY 指定，或先运行 setup.bat
    exit /b 1
)

REM data\ 不存在时首启写日志会失败（全新解压/换盘部署）
if not exist data mkdir data

REM 1) 后端服务：已在线则跳过
"%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" >nul 2>&1
if errorlevel 1 (
    echo [start] 启动后端服务 :8000 ...
    start "" /min "%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10
) else (
    echo [start] 后端已在运行，跳过
)

REM 2) ngrok 隧道：已在运行则跳过
tasklist | findstr /i "ngrok.exe" >nul 2>&1
if errorlevel 1 (
    if not exist "%NGROK%" (
        echo [!] 未找到 ngrok：%NGROK%
        echo     服务已在跑，但公网入口没起来 —— 手机连不上时先查这里
    ) else (
        echo [start] 启动 ngrok 隧道 %AI_PERSONA_URL% ...
        start "" /min "%NGROK%" http 8000 --url "%AI_PERSONA_URL%"
    )
) else (
    echo [start] ngrok 已在运行，跳过
)

echo [start] 部署机自启流程结束。手机访问 %AI_PERSONA_URL%
echo [start] 注意：ngrok 免费版首次访问会出现 ERR_NGROK_6024 提示页，需点 Visit Site。
exit /b 0