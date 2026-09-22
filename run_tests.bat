@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  AI RenXing System - offline test runner (unified entry)
rem  Runs all offline suites with the t2i-tts venv python,
rem  prints per-suite PASS/FAIL, exits non-zero if any fails.
rem
rem  Suites are DISCOVERED by glob so a newly added test_*.py runs
rem  in the gate automatically. Only suites that need real cloud API
rem  keys are skipped explicitly (SKIP_LIST) -- a hand-maintained
rem  allow list is how suites end up never running.
rem
rem  NOT covered here:
rem    - test_mimo.py / test_tts.py : need real cloud API keys
rem    - verify_chain.py / runtime_smoke.py : need a running service
rem ============================================================

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
cd /d "%~dp0"

if not exist "%PY%" (
    echo [ERROR] python not found: %PY%
    exit /b 1
)

set "SKIP_LIST=test_mimo test_tts"
set PASS_COUNT=0
set FAIL_COUNT=0
set SKIP_COUNT=0
set FAIL_LIST=

rem NOTE: the loop body lives in a :run_one subroutine on purpose.
rem A nested if/else inside a for-block (three levels of parentheses)
rem breaks cmd's parser with "else was unexpected at this time".
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
"%PY%" "%~2"
if errorlevel 1 (
    set /a FAIL_COUNT+=1
    set "FAIL_LIST=!FAIL_LIST! !N!"
    echo [SUITE FAIL] !N!
) else (
    set /a PASS_COUNT+=1
    echo [SUITE PASS] !N!
)
goto :eof
