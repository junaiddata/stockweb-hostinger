# VPS Sync Deployment Guide

Stock sync runs on the VPS via cron. The Office PC only runs the SSH tunnel so the VPS can reach the Integration API.

## Architecture

```
Office PC                    VPS
┌─────────────┐             ┌─────────────────────┐
│ Integration │◀─── tunnel ─│ localhost:8443       │
│ API         │   (ssh -R)  │                     │
└─────────────┘             │ cron: manage.py     │
                            │ sync_all            │
                            │        │            │
                            │        ▼            │
                            │  sync_stock_from_api│
                            │        │            │
                            │        ▼            │
                            │  SQLite databases   │
                            └─────────────────────┘
```

## Deploy Checklist

### 1. On VPS

- [ ] Deploy code
- [ ] `pip install python-dotenv` (or `pip install -r requirements.txt`)
- [ ] Copy `.env.example` to `.env` and set:
  ```
  API_BASE_HOST=http://localhost:8443
  API_TIMEOUT=60
  ```
- [ ] Ensure SSH tunnel is running on Office PC (see below)
- [ ] Configure cron (e.g. every 5 minutes):
  ```
  */5 * * * * /path/to/venv/bin/python /path/to/manage.py sync_all >> /var/log/sync_cron.log 2>&1
  ```
- [ ] Increase Gunicorn timeout for sync endpoints:
  ```
  gunicorn --timeout 120 app:app
  ```
  Or in systemd `ExecStart`: add `--timeout 120`
- [ ] Test: `python manage.py sync_all`
- [ ] Test ping: `curl https://stock.junaidworld.com/settings/sync/ping/`

### 2. On Office PC

- [ ] Run SSH tunnel (keep running):
  ```
  ssh -N -R 8443:192.168.1.103:80 user@VPS_IP
  ```
  Adjust `192.168.1.103` and `80` if your API uses a different host/port.
- [ ] Optional: Use `autossh` or a systemd service to auto-restart the tunnel

### 3. Stop PC Sync

- [ ] Disable/remove Task Scheduler job that ran `sync_stock_pc.py`
- [ ] Do not run `stockandso.bat` for stock sync (it now only shows instructions)

## Troubleshooting

| Issue | Check |
|-------|-------|
| Sync fails with "Connection failed" | SSH tunnel not running on PC; run `ssh -N -R 8443:...` |
| Sync fails with "timeout" | Increase `API_TIMEOUT` in .env; check tunnel/API speed |
| 500 on sync endpoints | Increase Gunicorn `--timeout` |
| Test routing | `curl https://your-domain/settings/sync/ping/` |
| Logs | `journalctl -u gunicorn_delivery -f` or `logs/sync_vps.log` |
