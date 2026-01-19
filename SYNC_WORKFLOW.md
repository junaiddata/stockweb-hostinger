# Stock Sync Workflow - PC to VPS

## Complete Workflow Explanation

### Setup Overview

```
┌─────────────┐         ┌──────────────┐         ┌──────────────┐
│  Your PC    │────────▶│  Local API   │         │   VPS        │
│ (Always On) │         │ 192.168.1.103│         │  (Hostinger) │
│             │         │              │         │              │
│ sync_stock_ │  Fetch  │ /Integration │  Push   │  app.py      │
│   pc.py     │────────▶│   Api/api/   │────────▶│  /api/sync-  │
│             │         │    Stock     │         │    stock     │
└─────────────┘         └──────────────┘         └──────────────┘
                                                         │
                                                         │ Updates
                                                         ▼
                                                 ┌──────────────┐
                                                 │  Databases   │
                                                 │  .db files   │
                                                 └──────────────┘
```

### Step-by-Step Workflow

1. **PC Script Runs** (via Windows Task Scheduler)
   - Script: `sync_stock_pc.py`
   - Runs automatically on your PC every X hours

2. **PC Fetches from Local API**
   - Connects to: `http://192.168.1.103/IntegrationApi/api/Stock`
   - Fetches data for warehouses "01" through "08"
   - Transforms data format
   - Calculates 15% margin for selling prices

3. **PC Sends to VPS**
   - Connects to: `https://your-vps-domain.com/api/sync-stock`
   - Sends JSON payload with warehouse code, items, and API key
   - VPS validates API key for security

4. **VPS Updates Databases**
   - Receives data from PC
   - Preserves admin-edited prices (if configured)
   - Updates appropriate database files:
     - `stock_data_headoffice.db` (DIP + retail branches)
     - `stock_data_rasalkhor.db` (RASALKHORE)

5. **VPS Returns Status**
   - PC script receives success/failure status
   - Logs results for each warehouse

## Setup Instructions

### Step 1: Configure PC Script

Edit `sync_stock_pc.py` and update these variables at the top:

```python
# Configuration - EDIT THESE
VPS_BASE_URL = "https://your-actual-vps-domain.com"  # Change this
VPS_API_KEY = "your-secure-random-key-here"  # Change this to a random string
```

**Important:**
- `VPS_BASE_URL`: Your actual VPS domain (e.g., `https://stock.yourdomain.com`)
- `VPS_API_KEY`: Generate a secure random string (e.g., `aBc123XyZ789SeCrEtKeY456`)

### Step 2: Configure VPS API Key

Edit `app.py` and find this line (around line 1320):

```python
VPS_API_KEY = "change-this-to-a-secure-random-key-12345"
```

Change it to **the SAME value** you used in `sync_stock_pc.py`:

```python
VPS_API_KEY = "your-secure-random-key-here"  # Must match PC script
```

### Step 3: Deploy VPS Changes

Upload the updated `app.py` to your VPS (Hostinger).

Make sure:
- The `/api/sync-stock` endpoint is accessible
- The API key matches what's in your PC script

### Step 4: Test Manually

1. **Test on PC:**
   ```bash
   cd "D:\dataanalyst\Data Analysis\STOCK WEB"
   python sync_stock_pc.py
   ```

2. **Check output:**
   - Should see success/failure for each warehouse
   - Should show items updated count

3. **Test on VPS:**
   - Check your stock pages
   - Verify data is updated

### Step 5: Setup Windows Task Scheduler

1. **Open Task Scheduler** (search in Windows Start)

2. **Create Basic Task:**
   - Name: "Stock Sync to VPS"
   - Trigger: Daily (or your preferred schedule)
   - Time: Choose when to run (e.g., every 4 hours)

3. **Action Settings:**
   - Action: "Start a program"
   - Program: `pythonw.exe` (or `python.exe` for visible window)
   - Arguments: `sync_stock_pc.py`
   - Start in: `D:\dataanalyst\Data Analysis\STOCK WEB`

4. **Advanced Settings (Optional):**
   - Check "Run whether user is logged on or not"
   - Check "Run with highest privileges" (if needed)
   - Set "Stop the task if it runs longer than": 1 hour

## Warehouse Mapping

| Warehouse Code | Branch | Database Column | Database File |
|---------------|--------|----------------|---------------|
| 01 | DIP | Stock Quantity | stock_data_headoffice.db |
| 02 | DIP | AJMAN | stock_data_headoffice.db |
| 03 | DIP | NAH | stock_data_headoffice.db |
| 04 | DIP | DEIRA | stock_data_headoffice.db |
| 05 | DIP | DEIRA2 | stock_data_headoffice.db |
| 06 | DIP | QUSAIS | stock_data_headoffice.db |
| 07 | DIP | ABUDHABI | stock_data_headoffice.db |
| 08 | RASALKHORE | Stock Quantity | stock_data_rasalkhor.db |

## Important Notes

### Price Calculation
- **All items** get **15% margin** applied: `Selling Price = AvgPrice × 1.15`
- Admin-edited prices are **preserved** (not overwritten) if `keep_admin_prices=True`

### Data Preservation
- When syncing warehouse "01" (DIP stock), retail branch columns (AJMAN, NAH, etc.) are preserved
- When syncing warehouses "02-07" (retail branches), DIP stock and other retail columns are preserved
- Admin price overrides are always preserved when `keep_admin_prices=True`

### Error Handling
- If one warehouse fails, others continue processing
- Network errors are logged and reported
- Database errors are caught and reported

## Troubleshooting

### "Failed to send to VPS"
- Check `VPS_BASE_URL` is correct
- Verify VPS is accessible from your PC
- Check firewall settings

### "Invalid API key"
- Ensure `VPS_API_KEY` matches in both `sync_stock_pc.py` and `app.py`
- Check for typos or extra spaces

### "No items updated"
- Check local API is accessible: `http://192.168.1.103/IntegrationApi/api/Stock`
- Verify warehouse code format ("01" not "1")
- Check API response format matches expected structure

### Script runs but no data updates
- Check VPS database file permissions
- Verify database paths in `app.py` (`DB_PATHS`)
- Check VPS logs for errors

## Manual Testing Commands

### Test PC Script:
```bash
python sync_stock_pc.py
```

### Test VPS Endpoint (using curl):
```bash
curl -X POST https://your-vps-domain.com/api/sync-stock \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "your-api-key",
    "warehouse_code": "01",
    "keep_admin_prices": true,
    "items": [{"ItemCode": "123", "OnHand": 10, "AvgPrice": 100}]
  }'
```

## Files Summary

- **`sync_stock_pc.py`**: Runs on your PC, fetches from local API, sends to VPS
- **`app.py`**: VPS Flask app with `/api/sync-stock` endpoint to receive data
- **`sync_stock.py`**: Original script (for same-machine sync, not needed for PC→VPS)

## Security Notes

1. **API Key**: Use a strong, random API key (at least 20 characters)
2. **HTTPS**: Ensure VPS uses HTTPS for secure data transfer
3. **Firewall**: Only allow access from your PC's IP if possible
4. **Monitoring**: Check logs regularly for unauthorized access attempts
