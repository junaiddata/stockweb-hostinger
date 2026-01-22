@echo off
REM Stop Stock Sync Service

echo Stopping Stock Sync Service...
echo.

REM Kill pythonw.exe processes running sync_stock_pc.py
taskkill /F /FI "WINDOWTITLE eq sync_stock_pc.py*" /T >nul 2>&1
taskkill /F /IM pythonw.exe /FI "COMMANDLINE eq *sync_stock_pc.py*" /T >nul 2>&1

REM Also try to kill by process name (if script name appears in command line)
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq pythonw.exe" /FO CSV ^| findstr /i "pythonw.exe"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "sync_stock_pc.py" >nul
    if !errorlevel! equ 0 (
        taskkill /F /PID %%a /T >nul 2>&1
    )
)

echo.
echo Service stopped (if it was running).
echo.
pause
