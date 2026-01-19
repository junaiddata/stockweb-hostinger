# Sync Script Logs Guide

## 📁 Log File Location

The sync script (`sync_stock_pc.py`) now automatically writes all output to a log file:

**Location:** `D:\dataanalyst\Data Analysis\STOCK WEB\sync_stock.log`

## 📖 How to View Logs

### Option 1: Open in Notepad
1. Navigate to: `D:\dataanalyst\Data Analysis\STOCK WEB\`
2. Double-click `sync_stock.log`
3. Scroll to bottom to see latest entries

### Option 2: View in Command Prompt
```cmd
cd "D:\dataanalyst\Data Analysis\STOCK WEB"
type sync_stock.log
```

### Option 3: View Last 50 Lines (PowerShell)
```powershell
cd "D:\dataanalyst\Data Analysis\STOCK WEB"
Get-Content sync_stock.log -Tail 50
```

### Option 4: Watch Log in Real-Time (PowerShell)
```powershell
cd "D:\dataanalyst\Data Analysis\STOCK WEB"
Get-Content sync_stock.log -Wait -Tail 20
```

## 📝 What's Logged

Every sync run logs:
- ✅ Start time
- ✅ Configuration (API URLs)
- ✅ Each warehouse sync status
- ✅ Success/failure for each warehouse
- ✅ Number of items updated
- ✅ Error messages (if any)
- ✅ Completion time
- ✅ Full traceback for errors

## 🔄 Log File Management

- **Auto-cleanup:** Log file automatically keeps last 1000 lines if it exceeds 5MB
- **No manual cleanup needed:** Old entries are automatically removed
- **Always appends:** New sync runs are added to the end of the file

## 📊 Example Log Entry

```
[2026-01-19 11:14:23] ======================================================================
[2026-01-19 11:14:23] PC Stock Sync Started: 2026-01-19 11:14:23
[2026-01-19 11:14:23] ======================================================================
[2026-01-19 11:14:23] Local API: http://192.168.1.103/IntegrationApi/api/Stock
[2026-01-19 11:14:23] VPS URL: http://localhost:5000
[2026-01-19 11:14:23] ----------------------------------------------------------------------
[2026-01-19 11:14:24] Processing warehouse 01...
[2026-01-19 11:14:25]   Sending 29647 items to VPS...
[2026-01-19 11:14:30]   ✓ Successfully synced 29647 items
[2026-01-19 11:14:30] Warehouse 01 (DIP/Stock Quantity): ✓ SUCCESS - 29647 items
[2026-01-19 11:14:30] Total: 8/8 warehouses synced successfully
[2026-01-19 11:14:30] Total items updated: 237176
[2026-01-19 11:14:30] ======================================================================
[2026-01-19 11:14:30] PC Stock Sync Completed: 2026-01-19 11:14:30
[2026-01-19 11:14:30] ======================================================================
```

## 🔍 Troubleshooting

### If log file doesn't exist:
- The script hasn't run yet, or
- There's a permission issue writing to the directory

### If you see errors in logs:
- Check the timestamp to see when it failed
- Look for error messages starting with `✗` or `ERROR`
- Check if Flask (`app.py`) is running
- Verify API connectivity

## 💡 Tips

1. **Check logs after Task Scheduler runs** - Open the log file to see if sync succeeded
2. **Monitor for errors** - Look for `✗ FAILED` or `ERROR` messages
3. **Check timestamps** - Verify sync is running every 2 minutes as scheduled
4. **Last entry shows status** - Scroll to bottom to see most recent sync result
