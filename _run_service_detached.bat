@echo off
REM AI-RenXing system port 8000 service - detached launcher (called by Task Scheduler)
REM Runs independent of any interactive session; logs go to data\
cd /d "%~dp0"

REM Hardcoded interpreter: when run as SYSTEM, %%USERPROFILE%% points to systemprofile
set "PY=C:\Users\31557\.openvino\venv\t2i-tts\Scripts\python.exe"
REM Keep TTS/ASR resident, avoid reloading models after idle
set "INTEL_SKILL_DOG_NO_EVICTION=1"

if not exist "%PY%" (
    echo [X] TTS python not found: %PY% >> data\service_err.log
    exit /b 1
)

"%PY%" -m uvicorn server:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 10 >> data\service.log 2>> data\service_err.log
