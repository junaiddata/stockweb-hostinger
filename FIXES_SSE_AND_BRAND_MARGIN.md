# Fixes: SSE HTTP/2 Error & Brand Margin Issues

## Issues Fixed

### 1. SSE HTTP/2 Protocol Error (`ERR_HTTP2_PROTOCOL_ERROR`)

**Problem:** Server-Sent Events (SSE) don't work properly with HTTP/2, causing connection errors on the VPS.

**Solution:** Updated Nginx configuration to:
- Force HTTP/1.1 for the SSE endpoint (`/api/stock-stream/`)
- Add proper headers to prevent HTTP/2 issues
- Ensure long-lived connections work correctly

**File Changed:** `NGINX_CONFIG_UPDATED.txt`

**What to do:**
1. Copy the updated Nginx config from `NGINX_CONFIG_UPDATED.txt`
2. Replace your current Nginx config file on the VPS
3. Test the configuration: `sudo nginx -t`
4. Reload Nginx: `sudo systemctl reload nginx`
5. Check browser console - SSE errors should be gone

---

### 2. Brand Margin Changes Not Reflecting

**Problem:** Brand margin changes weren't being applied because:
- Brand margin lookup was case-sensitive (e.g., "COSMO" vs "Cosmo" wouldn't match)
- Manufacturer names from API might have different casing than stored in database

**Solution:** 
- Made brand margin lookup **case-insensitive**
- Added debug logging to track when brand margins are applied
- Ensured brand margins apply to both DIP (warehouse 01) and RASALKHORE (warehouse 08)

**File Changed:** `app.py`

**What to do:**
1. Push the updated `app.py` to your VPS
2. Restart your Flask application
3. Run a sync from your PC
4. Check Flask logs to see brand margin debug messages (if margins differ from default)

---

## Testing

### Test SSE Fix:
1. Open stock page: `https://stock.junaidworld.com/stock/DIP`
2. Open browser console (F12)
3. You should see: `[SSE] Connection confirmed for branch: DIP`
4. No more `ERR_HTTP2_PROTOCOL_ERROR` errors

### Test Brand Margin Fix:
1. Change a brand margin in admin panel (e.g., set COSMO to 20%)
2. Run sync from PC: `python sync_stock_pc.py`
3. Check Flask logs on VPS for debug messages like:
   ```
   [Brand Margin] Item XXXXX: Manufacturer='COSMO', Margin=20%, Cost=100.00, Selling=125.00
   ```
4. Verify selling prices updated correctly on stock page

---

## Important Notes

- **Case-Insensitive Matching:** Brand margins now match regardless of case (e.g., "COSMO", "Cosmo", "cosmo" all match)
- **Admin Edits Preserved:** Manually edited prices are still preserved and NOT affected by brand margin changes
- **HTTP/1.1 Only:** SSE endpoint now uses HTTP/1.1 only (required for SSE to work)
- **No Performance Impact:** These changes don't affect page load speed or other endpoints

---

## If Issues Persist

### SSE Still Not Working:
1. Check Nginx error logs: `sudo tail -f /var/log/nginx/error.log`
2. Verify Flask is running: `sudo systemctl status stockweb` (or your service name)
3. Check if port/socket is correct in Nginx config

### Brand Margins Still Not Applying:
1. Check Flask logs for debug messages
2. Verify manufacturer name in database matches (case-insensitive now)
3. Ensure you're testing with warehouse 01 (DIP Stock Quantity) or warehouse 08 (RASALKHORE)
4. Check that brand margin was saved correctly in admin panel
