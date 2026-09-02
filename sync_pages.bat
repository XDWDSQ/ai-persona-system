@echo off
REM ============================================================
REM  前端同步：xiaoni-ai-persona/pages -> android assets
REM  改完前端跑一次本脚本，再 gradle assembleRelease 出新 APK。
REM  若检测到同步盘冲突副本则中止，先清理 *_冲突文件* 再同步。
REM ============================================================
cd /d "%~dp0"

dir /b xiaoni-ai-persona\pages\*冲突文件* >nul 2>&1
if not errorlevel 1 (
    echo [X] 检测到同步盘冲突副本，先清理 *_冲突文件* 再同步：
    dir /b xiaoni-ai-persona\pages\*冲突文件*
    pause
    exit /b 1
)

xcopy /E /Y /I xiaoni-ai-persona\pages android\app\src\main\assets\pages\ >nul
if errorlevel 1 (
    echo [X] 同步失败
    pause
    exit /b 1
)

echo [OK] 已同步到 android\app\src\main\assets\pages\
echo      如需更新 APK，继续执行: cd android ^&^& gradle assembleRelease
pause
