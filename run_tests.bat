@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  AI RenXing System - offline test runner (unified entry)
rem  Runs all offline suites with the t2i-tts venv python,
rem  prints per-suite PASS/FAIL, exits non-zero if any fails.
rem
rem  NOT included here (need real env):
rem    - test_mimo.py / test_tts.py : need real cloud API keys
rem    - verify_chain.py            : needs the service running (port 8000)
rem ============================================================

set "PY=%USERPROFILE%\.openvino\venv\t2i-tts\Scripts\python.exe"
cd /d "%~dp0"

if not exist "%PY%" (
    echo [ERROR] python not found: %PY%
    exit /b 1
)

set PASS_COUNT=0
set FAIL_COUNT=0
set FAIL_LIST=

for %%T in (test_role_engine test_server_helpers test_search test_attachments test_config_api test_tts_cache) do (
    echo.
    echo ==================== %%T ====================
    "%PY%" %%T.py
    if !errorlevel! == 0 (
        echo [SUITE PASS] %%T
        set /a PASS_COUNT+=1
    ) else (
        echo [SUITE FAIL] %%T
        set /a FAIL_COUNT+=1
        set "FAIL_LIST=!FAIL_LIST! %%T"
    )
)

echo.
echo ==================== SUMMARY ====================
echo PASS: !PASS_COUNT!   FAIL: !FAIL_COUNT!
if !FAIL_COUNT! gtr 0 (
    echo FAILED SUITES:!FAIL_LIST!
    exit /b 1
)
echo ALL OFFLINE TESTS PASSED.
exit /b 0
