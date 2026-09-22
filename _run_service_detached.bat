@echo off
REM AI-RenXing system port 8000 service - detached launcher (called by Task Scheduler)
REM Runs independent of any interactive session; logs go to data\
cd /d "%~dp0"

REM Interpreter resolution order:
REM   1) AI_PERSONA_PY (set it in the Task Scheduler task if you run as SYSTEM,
REM      where USERPROFILE points at systemprofile and has no venv)
REM   2) %USERPROFILE%\.openvino\venv\t2i-tts
REM This used to hardcode one machine's user directory, so the scheduled task
REM failed silently on every other box.
if not "%AI_PERSONA_PY%"=="" set "PY=%AI_PERSONA_PY%"
if not defined PY set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
REM Keep TTS/ASR resident, avoid reloading models after idle
set "INTEL_SKILL_DOG_NO_EVICTION=1"

REM data\ may not exist on a fresh clone; the redirects below fail without it
if not exist data mkdir data

if not exist "%PY%" (
    echo [X] TTS python not found: %PY%  ^(set AI_PERSONA_PY to override^) >> data\service_err.log
    exit /b 1
)

"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10 >> data\service.log 2>> data\service_err.log
