# Testing Sync Script Locally

## Quick Start Commands

### 1. Start Flask Server First
```bash
# Option 1: Direct Python
python app.py

# Option 2: Using Flask CLI
flask run --host=0.0.0.0 --port=5000

# Option 3: Using Gunicorn (if installed)
gunicorn -w 2 --bind 0.0.0.0:5000 app:app
```

### 2. Verify Flask is Running
```bash
# Check if port 5000 is listening
netstat -an | findstr :5000

# Or test with curl
curl http://localhost:5000

# Or open in browser
start http://localhost:5000
```

### 3. Run Sync Script in Local Mode
```bash
# One-time sync (recommended for testing)
python sync_stock_pc.py --local --once

# Continuous sync (runs every 5 minutes)
python sync_stock_pc.py --local
```

## Troubleshooting

### Error: "No connection could be made"
**Problem**: Flask server is not running on localhost:5000

**Solution**:
1. Open a new terminal window
2. Navigate to your project directory
3. Run: `python app.py`
4. Wait for "Running on http://0.0.0.0:5000"
5. Then run the sync script

### Error: "Connection refused"
**Problem**: Flask is running but not accessible

**Solution**:
- Make sure Flask is bound to `0.0.0.0` not just `127.0.0.1`
- Check firewall settings
- Verify port 5000 is not blocked

### Check if Flask is Running
```bash
# Windows PowerShell
Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue

# Check Flask process
Get-Process python | Where-Object {$_.Path -like "*python*"}
```

## Testing Workflow

1. **Terminal 1** - Start Flask:
   ```bash
   cd "D:\dataanalyst\Data Analysis\STOCK WEB"
   python app.py
   ```

2. **Terminal 2** - Run Sync:
   ```bash
   cd "D:\dataanalyst\Data Analysis\STOCK WEB"
   python sync_stock_pc.py --local --once
   ```

3. **Check Results**:
   - Check Flask terminal for sync logs
   - Check `sync_stock.log` for detailed logs
   - Verify data in your database

## Expected Output

### Successful Sync:
```
🔧 LOCAL MODE: Using http://localhost:5000 instead of https://stock.junaidworld.com
======================================================================
PC Stock Sync Started: 2026-02-18 15:08:06
======================================================================
Local API: http://192.168.1.103/IntegrationApi/api/Stock
VPS URL: http://localhost:5000
----------------------------------------------------------------------
✓ Connected to http://localhost:5000

Processing warehouse 01...
  Sending 29275 items to VPS...
  ✓ Successfully synced 29275 items
```

### If Flask Not Running:
```
❌ ERROR: Cannot connect to http://localhost:5000

💡 Flask server is not running!
   Start Flask with: python app.py
   Or: flask run --host=0.0.0.0 --port=5000
```
