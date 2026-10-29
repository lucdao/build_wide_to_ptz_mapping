@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Calibrate.ps1" %*
if "%errorlevel%"=="130" exit /b 0
if errorlevel 1 (
    echo.
    echo Chua hoan tat. Xem thong bao loi o tren.
    pause
    exit /b 1
)
