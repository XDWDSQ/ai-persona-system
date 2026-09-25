@echo off
REM AI-RenXing system port 8000 service - detached launcher (called by Task Scheduler)
REM Runs independent of any interactive session; logs go to data\
REM
REM Interpreter resolution order:
REM   1) AI_PERSONA_PY   - set it in the Task Scheduler task if you run as SYSTEM,
REM                        where USERPROFILE points at systemprofile and has no venv
REM   2) %USERPROFILE%\.openvino\venv\t2i-tts  - legacy TTS env on the original box
REM   3) _find_python.bat - PYTHON env var -> project venv -> PATH (shared logic)
REM Keep TTS/ASR resident, avoid reloading models after idle
cd /d "%~dp0"

set "PY="
set "PYTHON="
if not "%AI_PERSONA_PY%"=="" set "PY=%AI_PERSONA_PY%"
if not defined PY if exist "%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe" set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
REM NOTE: no "cmd || set PY=" one-liners here - a failing %PY% would leak a
REM non-zero errorlevel onto the next command in the chain.
if defined PY (
    "%PY%" -c "import uvicorn" >nul 2>&1
    if errorlevel 1 set "PY="
)
if not defined PY call "%~dp0_find_python.bat" uvicorn
if defined PYTHON set "PY=%PYTHON%"

set "INTEL_SKILL_DOG_NO_EVICTION=1"

REM data\ may not exist on a fresh clone; the redirects below fail without it
if not exist data mkdir data

if not defined PY (
    echo [X] backend python not found ^(set AI_PERSONA_PY to override^) >> data\service_err.log
    exit /b 1
)

"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10 >> data\service.log 2>> data\service_err.log