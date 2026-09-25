@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  AI RenXing System - offline test runner (unified entry)
REM
REM  Runs every offline suite (test_*.py in this folder) and prints
REM  per-suite PASS/FAIL. Exits non-zero if any suite fails.
REM
REM  Suites are DISCOVERED by glob, so a newly added test_*.py joins
REM  the gate automatically. Only suites needing real cloud API keys
REM  are skipped explicitly (SKIP_LIST) - a hand-maintained allow
REM  list is how suites end up never running.
REM
REM  NOT covered here:
REM    - test_mimo.py / test_tts.py : need real cloud API keys
REM    - ops/heic_upload_e2e_test.py : needs a live service on :8000
REM    - verify_chain.py / runtime_smoke.py : need a live service
REM
REM  The interpreter is located by _find_python.bat (PYTHON env var ->
REM  project venv -> PATH). Ninth round fix: this file used to
REM  hardcode %USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe,
REM  so on any other machine the whole gate was a no-op.
REM
REM  If this script mysteriously dies with things like
REM  "'matically.' is not recognized as an internal or external command",
REM  the file lost its CRLF line endings. cmd.exe requires CRLF in .bat;
REM  an LF-only file gets byte-misparsed around multi-byte comments.
REM  See .gitattributes - never let an editor rewrite these as LF.
REM ============================================================

cd /d "%~dp0"

REM Offline mode: TestClient starts the real lifespan, and with a real
REM config an active role with auto-refresh would really call search +
REM cloud LLM on startup (burning tokens). Cut all server-initiated
REM external calls; /api/status provider probing degrades to "not
REM probed" and sends no HTTP at all.
set "AI_DISABLE_EXTERNAL=1"

call "%~dp0_find_python.bat" pytest
if not defined PYTHON (
    echo [ERROR] no usable python found.
    echo         %PYTHON_ERR%
    echo         fix: run setup.bat, or set PYTHON=^<path to python.exe^>
    exit /b 1
)
echo [INFO] python = %PYTHON%

set "SKIP_LIST=test_mimo test_tts"
set PASS_COUNT=0
set FAIL_COUNT=0
set SKIP_COUNT=0
set FAIL_LIST=

REM NOTE: the loop body lives in a :run_one subroutine on purpose.
REM A nested if/else inside a for-block (three levels of parentheses)
REM breaks cmd's parser with "else was unexpected at this time".
for %%F in (test_*.py) do call :run_one "%%~nF" "%%F"

echo.
echo ==================== SUMMARY ====================
echo PASS: !PASS_COUNT!   FAIL: !FAIL_COUNT!   SKIP: !SKIP_COUNT!
if !PASS_COUNT! equ 0 (
    echo [ERROR] no suite ran - the glob or the working directory is wrong
    exit /b 1
)
if !FAIL_COUNT! gtr 0 (
    echo FAILED SUITES:!FAIL_LIST!
    exit /b 1
)
echo ALL OFFLINE TESTS PASSED.
exit /b 0

:run_one
set "N=%~1"
for %%S in (%SKIP_LIST%) do if /I "%%S"=="!N!" (
    set /a SKIP_COUNT+=1
    echo [SUITE SKIP] !N!  - needs real cloud API keys
    goto :eof
)
echo.
echo ==================== !N! ====================
"%PYTHON%" "%~2"
if errorlevel 1 (
    set /a FAIL_COUNT+=1
    set "FAIL_LIST=!FAIL_LIST! !N!"
    echo [SUITE FAIL] !N!
) else (
    set /a PASS_COUNT+=1
    echo [SUITE PASS] !N!
)
goto :eof