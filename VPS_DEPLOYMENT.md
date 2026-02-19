# VPS Deployment Guide – "Database is temporarily busy" Fix

## Root Cause

**Gunicorn with multiple workers + SQLite** causes "database is locked" or "database is busy" errors.  
SQLite uses file-level locking. Each Gunicorn worker is a separate process. Multiple processes accessing the same `.db` file leads to locks.

- **Local**: Running `python app.py` uses a single process → no conflict.
- **VPS**: Gunicorn with `-w 4` (4 workers) = 4 processes hitting the same SQLite files → locks.

## Fix: Use 1 Gunicorn Worker

### Option A: Use the config file (recommended)

```bash
cd /path/to/STOCK\ WEB
gunicorn -c gunicorn_config.py app:app
```

### Option B: Command line

```bash
gunicorn -w 1 -t 300 --bind 0.0.0.0:5000 app:app
```

### Update systemd service (if you use one)

Edit your service file (e.g. `/etc/systemd/system/stock-web.service`):

```ini
[Service]
ExecStart=/usr/bin/gunicorn -c gunicorn_config.py app:app
# Or: ExecStart=/usr/bin/gunicorn -w 1 -t 300 --bind 0.0.0.0:5000 app:app
WorkingDirectory=/path/to/STOCK WEB
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart stock-web
```

### Update Nginx proxy (if used)

Ensure `proxy_read_timeout` is high enough (e.g. 300s) so long syncs don’t trigger 504s.

## What Changed in This Fix

1. **`gunicorn_config.py`** – `workers=1` for SQLite compatibility.
2. **`app.py`** – `DB_PATHS` use absolute paths so the app works regardless of working directory.
3. **Error logging** – 500 errors are written to stderr so Gunicorn logs show the real traceback.

## Verify After Deploy

1. Restart the app with the new config.
2. Test search on headoffice, rasalkhor, alabama.
3. If errors persist, check Gunicorn logs for the full traceback:

   ```bash
   journalctl -u stock-web -f
   # or
   tail -f /var/log/gunicorn/error.log
   ```

## Sync + Search at the Same Time

With 1 worker and WAL mode, search should work even while sync runs. If you still see occasional "Database is temporarily busy", you can reduce sync frequency in `sync_stock_pc.py` (e.g. every 10–15 minutes instead of 5).
