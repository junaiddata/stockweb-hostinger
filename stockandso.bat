@echo off
REM ============================================
REM Stock & Sales Orders - VPS Sync Mode
REM ============================================
REM Stock sync now runs on VPS via cron (manage.py sync_all).
REM PC only needs to run the SSH tunnel so VPS can reach the API.
REM
REM Run this on Office PC (replace with your values):
REM   ssh -N -R 8443:192.168.1.103:80 user@VPS_IP
REM
REM Sales orders sync (if used) - run from its folder.
REM ============================================

echo VPS Sync Mode - No local sync needed.
echo.
echo Ensure SSH tunnel is running on this PC:
echo   ssh -N -R 8443:192.168.1.103:80 user@VPS_IP
echo.
echo Stock sync runs on VPS via: python manage.py sync_all
echo.

pause
