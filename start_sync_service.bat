@echo off
REM Start Stock Sync Service in Background
REM This will run continuously and sync every 5 minutes

echo Starting Stock Sync Service...
echo.
echo The service will:
echo - Run continuously in the background
echo - Sync every 5 minutes automatically
echo - Log everything to sync_stock.log
echo.
echo To stop: Open Task Manager and end pythonw.exe process
echo.

REM Start the service using pythonw.exe (no window)
start "" pythonw.exe sync_stock_pc.py

echo Service started!
echo.
echo Check sync_stock.log for status updates.
pause
