@echo off
REM ============================================
REM Stock & Sales Orders Sync Launcher
REM ============================================
REM - Runs stock sync from current directory
REM - Runs sales orders sync from another folder
REM - No directory changes
REM - No Python code changes
REM ============================================

echo Starting background sync services...
echo.

REM --- Stock sync (current directory) ---
echo Starting Stock Sync Service...
start "" pythonw.exe sync_stock_pc.py

REM --- Sales orders sync (absolute path) ---
echo Starting Sales Orders Sync Service...
start "" pythonw.exe "D:\dataanalyst\salesorder-web\salesorder\sync_salesorders_pc.py"

echo.
echo Both services started successfully.
echo.
echo To stop them, open Task Manager and end pythonw.exe
echo.

pause
