@echo off
REM ============================================================
REM  AI RenXing System - shared python interpreter locator
REM  (called via "call _find_python.bat [requiredModule]")
REM
REM  Why this file exists: start.bat / setup.bat / restart_service.bat
REM  and run_tests.bat used to hardcode
REM    %USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe
REM  which only ever existed on the original dev box. After a machine
REM  move or the TTS env being deleted, every entry point died with a
REM  raw traceback (or a misleading "run setup.bat" message) while a
REM  perfectly good project venv sat next to them, unused.
REM
REM  Discovery order (first candidate that passes validation wins):
REM    1) %PYTHON%         - explicit override (CI / unusual setups)
REM    2) .\venv\Scripts\python.exe
REM    3) .\.venv\Scripts\python.exe
REM    4) python.exe on PATH
REM
REM  Validation per candidate:
REM    a) the interpreter actually runs (catches venv whose base
REM       python was uninstalled - python.exe resolves its own DLL
REM       and dies with "unable to load python3xx.dll")
REM    b) it can import the optional required module passed as %1,
REM       skipping the "No module named <argv0>" false positive.
REM
REM  Outputs (for the caller):
REM    PYTHON      - full path to a usable interpreter, or unset
REM    PYTHON_ERR  - human readable reason when PYTHON is unset
REM  The caller decides how loud to be; this file never exits /b.
REM ============================================================

set "PYTHON="
set "PYTHON_ERR="
set "PY_REQ=%~1"
set "PY_ROOT=%~dp0"

call :try_py "%PYTHON%"          "PYTHON env var"
if not defined PYTHON call :try_py "%PY_ROOT%venv\Scripts\python.exe"  "project venv"
if not defined PYTHON call :try_py "%PY_ROOT%.venv\Scripts\python.exe" "project .venv"
if not defined PYTHON call :try_py "python.exe"                        "python on PATH"

if not defined PYTHON if not defined PYTHON_ERR set "PYTHON_ERR=no configurable python interpreter found"
goto :eof

REM ------------------------------------------------------------------
REM  :try_py  <path>  <label>
REM  Validates a candidate; on success sets PYTHON and clears PYTHON_ERR.
REM ------------------------------------------------------------------
:try_py
set "A2P_CAND=%~1"
set "A2P_LABEL=%~2"
if not defined A2P_CAND goto :eof

REM --- a) interpreter runs at all ---
"%A2P_CAND%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    set "PYTHON_ERR=%A2P_LABEL% is not runnable: %A2P_CAND%"
    goto :eof
)

REM --- b) optional required module ---
if not defined PY_REQ goto :try_py_ok
set "A2P_TRY="
for /f "tokens=3" %%a in ('""%A2P_CAND%" -c "import %PY_REQ%" 2^>^&1"') do (
    if /i "%%a"=="named" set "A2P_TRY=%%~b"
)
REM "No module named 'x'" -> %%a=named %%b=x. The interpreter itself
REM prints "No module named <argv0>" (no quotes) when given a path it
REM cannot run, so an unquoted token would be a false positive.
if defined A2P_TRY (
    set "PYTHON_ERR=%A2P_LABEL% cannot import %PY_REQ%: %A2P_CAND%"
    goto :eof
)

:try_py_ok
set "PYTHON=%A2P_CAND%"
set "PYTHON_ERR="
goto :eof
