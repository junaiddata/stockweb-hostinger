# Real-Time Stock Updates via SSE (Server-Sent Events) - Workflow Documentation

## 📋 Overview

This document explains how the real-time stock update system works using **Server-Sent Events (SSE)**. When stock data is synced from the local API, all open stock pages automatically refresh to show the latest data **without manual page refresh**.

---

## 🏗️ Architecture

### Components

1. **Flask App (`app.py`)** - Web server with SSE endpoints
2. **Sync Script (`sync_stock_pc.py`)** - Fetches data and notifies Flask
3. **Frontend (`stock.html`)** - JavaScript connects to SSE stream
4. **SSE Connection Manager** - Manages active browser connections

### Data Flow

```
┌─────────────────┐
│  Sync Script    │
│ (sync_stock_pc) │
└────────┬────────┘
         │
         │ 1. Syncs data to Flask
         ▼
┌─────────────────┐
│   Flask App     │
│   (app.py)      │
│                 │
│  - Updates DB   │
│  - Broadcasts   │
│    SSE update   │
└────────┬────────┘
         │
         │ 2. Sends SSE message
         │    to all browsers
         ▼
┌─────────────────┐
│   Browsers      │
│  (stock.html)   │
│                 │
│  - Receives     │
│    update       │
│  - Auto-refresh │
└─────────────────┘
```

---

## 🔧 Changes Made

### 1. Flask App (`app.py`)

#### Added Imports
```python
from flask import Response
import threading
import queue
import json
```

#### Added SSE Connection Management
- **`sse_connections`**: Dictionary storing active SSE connections per branch
- **`sse_lock`**: Thread lock for thread-safe access
- **`broadcast_sse_update()`**: Function to send updates to all connected browsers

#### Added SSE Endpoints

**`/api/stock-stream/<branch>`** (GET)
- SSE endpoint that browsers connect to
- Maintains persistent connection
- Sends updates when sync completes
- Sends keep-alive pings every 30 seconds

**`/api/notify-sync-complete`** (POST)
- Called by sync script after sync completes
- Triggers SSE broadcast to all connected browsers
- Accepts: `branch`, `warehouse_code`, `items_updated`

#### Modified Sync Endpoint
- **`/api/sync-stock`**: Now broadcasts SSE update after successful sync

#### Updated Middleware
- Added exceptions for SSE endpoints (no device check required)

---

### 2. Sync Script (`sync_stock_pc.py`)

#### Added Function
**`notify_flask_updates(results)`**
- Called after all warehouses are synced
- Groups results by branch
- Sends notification to Flask for each successful warehouse sync
- Non-blocking: Sync doesn't fail if notification fails

#### Modified Main Function
- Calls `notify_flask_updates()` after sync completes
- Logs notification status

---

### 3. Frontend (`templates/stock.html`)

#### Added JavaScript
- **SSE Connection Manager**: Connects to `/api/stock-stream/<branch>`
- **Auto-Reconnect**: Reconnects if connection drops
- **Visibility Detection**: Only connects when page is visible
- **Update Handler**: Receives sync completion notifications
- **Auto-Refresh**: Reloads page 2 seconds after update (preserves search/filters)
- **Visual Notification**: Shows toast notification when update received

#### Features
- ✅ Connects automatically on page load
- ✅ Reconnects if connection drops
- ✅ Only active when page is visible (saves resources)
- ✅ Shows notification when update received
- ✅ Auto-refreshes to show new data
- ✅ Preserves search query and filters

---

## 🔄 Workflow Steps

### Step 1: User Opens Stock Page
1. Browser loads `stock.html`
2. JavaScript detects page load
3. Creates EventSource connection to `/api/stock-stream/<branch>`
4. Flask adds connection to `sse_connections[branch]`
5. Browser receives connection confirmation

### Step 2: Sync Script Runs (Every 2 Minutes)
1. `sync_stock_pc.py` runs via Task Scheduler
2. Fetches data from local API (`192.168.1.103`)
3. Sends data to Flask `/api/sync-stock` endpoint
4. Flask updates database
5. Flask broadcasts SSE update to all connected browsers
6. Sync script calls `/api/notify-sync-complete` (backup notification)

### Step 3: Browser Receives Update
1. Browser receives SSE message via EventSource
2. JavaScript shows notification: "Stock updated: X items synced"
3. After 2 seconds, page auto-refreshes (preserving search/filters)
4. User sees latest stock data automatically

---

## 📊 Technical Details

### SSE Connection Lifecycle

```
Browser                    Flask Server
   │                            │
   │─── GET /api/stock-stream/DIP ───>│
   │                            │
   │<─── SSE Stream (connected) ────│
   │                            │
   │                            │ (Sync completes)
   │                            │
   │<─── SSE Message (update) ────│
   │                            │
   │ (Auto-refresh page)        │
   │                            │
```

### Message Format

**Connection Confirmation:**
```json
{
  "type": "connected",
  "branch": "DIP"
}
```

**Sync Update:**
```json
{
  "type": "sync_complete",
  "warehouse_code": "01",
  "branch": "DIP",
  "items_updated": 29647,
  "timestamp": "2026-01-19T11:14:30"
}
```

### Keep-Alive Mechanism
- SSE sends keep-alive ping every 30 seconds
- Prevents connection timeout
- Browser automatically reconnects if connection drops

---

## 🎯 Benefits

### For Users
- ✅ **No Manual Refresh**: Page updates automatically
- ✅ **Real-Time Data**: See latest stock within seconds of sync
- ✅ **Visual Feedback**: Notification shows when update received
- ✅ **Seamless Experience**: Search/filters preserved on refresh

### For System
- ✅ **Efficient**: Only updates when sync actually happens
- ✅ **Scalable**: Handles multiple browsers per branch
- ✅ **Resilient**: Auto-reconnects if connection drops
- ✅ **Resource-Friendly**: Only connects when page visible

---

## 🔍 Troubleshooting

### Issue: Page Not Auto-Updating

**Check:**
1. Open browser console (F12)
2. Look for SSE connection messages:
   - `[SSE] Connected to real-time updates` ✅
   - `[SSE] Connection error` ❌

**Solutions:**
- Check Flask is running
- Check network connectivity
- Check browser console for errors
- Verify sync script is running and completing successfully

### Issue: Multiple Notifications

**Cause:** Sync script calls both:
1. Flask's `/api/sync-stock` (broadcasts automatically)
2. Flask's `/api/notify-sync-complete` (backup notification)

**Solution:** This is intentional redundancy. Both are safe to call.

### Issue: Connection Drops Frequently

**Check:**
- Network stability
- Flask server stability
- Browser compatibility (modern browsers required)

**Solution:** JavaScript automatically reconnects after 5 seconds.

---

## 📝 Configuration

### Sync Interval
Currently: **Every 2 minutes** (configured in Task Scheduler)

### Auto-Refresh Delay
Currently: **2 seconds** after update received
- Location: `stock.html` JavaScript
- Can be adjusted in `handleSyncUpdate()` function

### SSE Keep-Alive Interval
Currently: **30 seconds**
- Location: `app.py` `stock_stream()` function
- Can be adjusted in `q.get(timeout=30)`

---

## 🚀 Testing

### Test SSE Connection
1. Open stock page
2. Open browser console (F12)
3. Should see: `[SSE] Connected to real-time updates`

### Test Auto-Update
1. Open stock page
2. Wait for sync to complete (check sync log)
3. Should see notification and page refresh automatically

### Test Reconnection
1. Open stock page
2. Stop Flask server
3. Should see connection error in console
4. Start Flask server
5. Should reconnect automatically after 5 seconds

---

## 📚 Additional Notes

### Browser Compatibility
- ✅ Chrome/Edge (latest)
- ✅ Firefox (latest)
- ✅ Safari (latest)
- ❌ Internet Explorer (not supported)

### Performance Impact
- **Minimal**: Only sends updates when sync happens
- **Efficient**: One connection per browser
- **Scalable**: Handles hundreds of connections

### Security
- SSE endpoints bypass device restriction middleware
- No authentication required (public stock data)
- Sync notification endpoint is public (called by sync script)

---

## 🔄 Future Enhancements

Possible improvements:
1. **Partial Updates**: Update only changed rows instead of full page refresh
2. **WebSocket**: Switch to WebSocket for bidirectional communication
3. **Filtered Updates**: Only send updates for visible/search results
4. **Update History**: Show sync history in UI
5. **Connection Status**: Show connection status indicator

---

## 📞 Support

If you encounter issues:
1. Check `sync_stock.log` for sync status
2. Check browser console for JavaScript errors
3. Check Flask logs for server errors
4. Verify sync script is running successfully

---

**Last Updated:** 2026-01-19
**Version:** 1.0
