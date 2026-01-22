# Background Sync Service Guide

## Overview

The sync script (`sync_stock_pc.py`) can now run as a **background service** that automatically syncs every 5 minutes, without needing Task Scheduler.

---

## Installation

### 1. Install Required Package

```bash
pip install schedule
```

Or install all requirements:
```bash
pip install -r requirements.txt
```

---

## Running the Service

### Option A: Background Service (No Terminal Window) - **RECOMMENDED**

Use `pythonw.exe` (Windows background Python) to run without a visible window:

```bash
pythonw.exe sync_stock_pc.py
```

**To stop:** Open Task Manager → Find `pythonw.exe` → End Task

---

### Option B: Visible Terminal Window

If you want to see the logs in real-time:

```bash
python sync_stock_pc.py
```

**To stop:** Press `Ctrl+C` in the terminal

---

### Option C: One-Time Run (For Testing)

To run once and exit (like before):

```bash
python sync_stock_pc.py --once
```

---

## How It Works

1. **Starts immediately:** Runs sync on startup (no 5-minute wait)
2. **Schedules automatically:** Then runs every 5 minutes
3. **Runs continuously:** Keeps running until stopped
4. **Logs everything:** All output goes to `sync_stock.log`

---

## Starting on Windows Boot (Optional)

If you want the service to start automatically when Windows boots:

### Method 1: Startup Folder (Easiest)

1. Press `Win + R`
2. Type `shell:startup` and press Enter
3. Create a shortcut to:
   ```
   C:\Python313\pythonw.exe "D:\dataanalyst\Data Analysis\STOCK WEB\sync_stock_pc.py"
   ```
   (Adjust paths to match your Python and script locations)

### Method 2: Task Scheduler (More Control)

1. Open Task Scheduler
2. Create Basic Task
3. Name: "Stock Sync Service"
4. Trigger: "When the computer starts"
5. Action: Start a program
6. Program: `C:\Python313\pythonw.exe`
7. Arguments: `"D:\dataanalyst\Data Analysis\STOCK WEB\sync_stock_pc.py"`
8. ✅ Check "Run whether user is logged on or not"
9. ✅ Check "Run with highest privileges" (if needed)

---

## Checking if Service is Running

### Method 1: Check Log File

```bash
# View last 20 lines
tail -n 20 sync_stock.log

# Or on Windows PowerShell:
Get-Content sync_stock.log -Tail 20
```

### Method 2: Task Manager

1. Open Task Manager (`Ctrl + Shift + Esc`)
2. Go to "Details" tab
3. Look for `pythonw.exe` or `python.exe` running `sync_stock_pc.py`

---

## Stopping the Service

### If running with `pythonw.exe`:

1. Open Task Manager (`Ctrl + Shift + Esc`)
2. Go to "Details" tab
3. Find `pythonw.exe` (or `python.exe`)
4. Right-click → End Task

### If running in terminal:

Press `Ctrl+C`

---

## Log File Location

Logs are saved to:
```
D:\dataanalyst\Data Analysis\STOCK WEB\sync_stock.log
```

The log file will show:
- Each sync run timestamp
- Success/failure for each warehouse
- Any errors
- Total items synced

---

## Troubleshooting

### Service Not Starting

1. **Check Python path:**
   ```bash
   pythonw.exe --version
   ```

2. **Check script path:**
   Make sure the full path to `sync_stock_pc.py` is correct

3. **Check log file:**
   Look for errors in `sync_stock.log`

### Service Stops Unexpectedly

1. **Check log file** for errors
2. **Check network connection** (API/VPS connectivity)
3. **Restart the service** manually

### Multiple Instances Running

If you see multiple `pythonw.exe` processes:
1. Stop all instances (Task Manager)
2. Start fresh with `pythonw.exe sync_stock_pc.py`

---

## Advantages Over Task Scheduler

✅ **No password needed** - Runs in background without user login  
✅ **Continuous operation** - Keeps running even if one sync fails  
✅ **Better error handling** - Logs all errors, doesn't stop on failure  
✅ **Immediate first run** - Syncs on startup, not after 5 minutes  
✅ **Easier to manage** - Just start/stop the script  
✅ **No Windows account issues** - Works with any user account  

---

## Summary

**To start background service:**
```bash
pythonw.exe sync_stock_pc.py
```

**To stop:**
- Task Manager → End `pythonw.exe` process

**To check status:**
- View `sync_stock.log` file

**That's it!** No Task Scheduler needed. 🎉
