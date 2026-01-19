# Testing Locally (Localhost)

## Quick Setup for Local Testing

### Step 1: Update API Key in Both Files

**In `sync_stock_pc.py` (line ~24):**
```python
VPS_API_KEY = "test-key-12345"  # Already set for testing
```

**In `app.py` (line ~1315):**
```python
VPS_API_KEY = "test-key-12345"  # Already set for testing
```

✅ **Already configured!** Both files now use `"test-key-12345"` for testing.

### Step 2: Update VPS URL for Localhost

**In `sync_stock_pc.py` (line ~23):**
```python
VPS_BASE_URL = "http://localhost:5000"  # Already set for testing
```

✅ **Already configured!** The script will now connect to your local Flask app.

### Step 3: Run Flask App Locally

Open a terminal/command prompt and run:

```bash
cd "D:\dataanalyst\Data Analysis\STOCK WEB"
python app.py
```

You should see:
```
 * Running on http://127.0.0.1:5000
 * Running on http://0.0.0.0:5000
```

**Keep this terminal open!** The Flask app must be running.

### Step 4: Test the Sync Script

Open a **NEW** terminal/command prompt and run:

```bash
cd "D:\dataanalyst\Data Analysis\STOCK WEB"
python sync_stock_pc.py
```

### Expected Output

You should see:
```
======================================================================
PC Stock Sync Started: 2024-XX-XX XX:XX:XX
======================================================================
Local API: http://192.168.1.103/IntegrationApi/api/Stock
VPS URL: http://localhost:5000
----------------------------------------------------------------------

Processing warehouse 01...
Processing warehouse 02...
...

Sync Summary:
----------------------------------------------------------------------
Warehouse 01 (DIP/Stock Quantity): ✓ SUCCESS - 1234 items
Warehouse 02 (DIP/AJMAN): ✓ SUCCESS - 567 items
...
----------------------------------------------------------------------

Total: 8/8 warehouses synced successfully
Total items updated: 29647
======================================================================
```

### Step 5: Verify Data Updated

1. Open your browser: `http://localhost:5000`
2. Navigate to stock pages
3. Check if data is updated

---

## Troubleshooting

### Error: "Connection refused" or "Failed to send to VPS"

**Problem:** Flask app is not running

**Solution:**
- Make sure `app.py` is running in another terminal
- Check it's running on port 5000
- Try accessing `http://localhost:5000` in browser first

### Error: "Invalid API key"

**Problem:** API keys don't match

**Solution:**
- Check `VPS_API_KEY` in both `sync_stock_pc.py` and `app.py`
- They must be **exactly the same** (case-sensitive)
- Currently both should be: `"test-key-12345"`

### Error: "Failed to fetch from local API"

**Problem:** Cannot access `http://192.168.1.103`

**Solution:**
- Make sure your local API server is running
- Check network connectivity
- Verify the API URL is correct

### Script runs but no data appears

**Problem:** Database files not found or wrong path

**Solution:**
- Check database files exist: `stock_data_headoffice.db`, `stock_data_rasalkhor.db`
- Make sure you're running from the correct directory
- Check file permissions

---

## Testing Checklist

- [ ] Flask app (`app.py`) is running on `http://localhost:5000`
- [ ] `VPS_API_KEY` matches in both files (`"test-key-12345"`)
- [ ] `VPS_BASE_URL` is set to `"http://localhost:5000"` in `sync_stock_pc.py`
- [ ] Local API is accessible at `http://192.168.1.103/IntegrationApi/api/Stock`
- [ ] Database files exist in the project directory
- [ ] Sync script runs without errors
- [ ] Data appears in web interface

---

## When Ready for Production

1. **Change API Key:**
   - Generate a secure random key (at least 20 characters)
   - Update in both `sync_stock_pc.py` and `app.py`

2. **Change VPS URL:**
   - In `sync_stock_pc.py`, change:
   ```python
   VPS_BASE_URL = "https://your-actual-vps-domain.com"
   ```

3. **Deploy to VPS:**
   - Upload updated `app.py` to your Hostinger VPS
   - Make sure the API key matches

4. **Test from PC:**
   - Run `sync_stock_pc.py` from your PC
   - It should now connect to your VPS instead of localhost

---

## Current Configuration (For Testing)

✅ **sync_stock_pc.py:**
- `VPS_BASE_URL = "http://localhost:5000"`
- `VPS_API_KEY = "test-key-12345"`

✅ **app.py:**
- `VPS_API_KEY = "test-key-12345"`

Both are ready for local testing!
