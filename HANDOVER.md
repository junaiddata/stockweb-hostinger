# Project Handover — Stock Web (Stock Management App)

> A Flask web application for searching and managing warehouse/retail stock across
> multiple branches. Stock data is synced from an external ERP **API** (via an SSH
> tunnel) into local **SQLite** databases, and served through a set of branch pages
> with live (SSE) updates, admin pricing controls, and device-based access control.

This document is written for a new developer taking over the project. Read it top to
bottom once, then use it as a reference. **Everything below reflects the actual code in
this repo as of handover — verify line numbers if the code has since changed.**

---

## 1. Quick Facts

| Item | Value |
|------|-------|
| Language / Framework | Python 3, **Flask 3.1** |
| Data store | **SQLite** (multiple `.db` files, one per branch group) |
| Frontend | Jinja2 server-rendered HTML templates + a shared CSS file |
| Real-time | Server-Sent Events (SSE) |
| Data source | External ERP **IntegrationApi/api/Stock** (reached via SSH reverse tunnel) |
| Production server | **Gunicorn** (1 worker, threaded) behind **Nginx**, on a Linux **VPS** |
| Main app file | `app.py` (~5,000 lines — the whole backend lives here) |
| Dev entry | `python app.py` → http://0.0.0.0:5000 (debug mode) |

⚠️ **The entire backend is a single file: `app.py`.** There is no package structure,
no blueprints, no ORM. Routes, DB helpers, sync logic, pricing logic and SSE all live
in that one module. Get comfortable with it — search by the section headers below.

---

## 2. What the App Does (Business Context)

The company has stock in several warehouses and retail stores. This app lets staff and
the public:

1. **Search stock** by item code / description / brand for a branch, see quantity and a
   computed **selling price**.
2. Selling price is **not** stored raw — it is computed from a **cost price** plus a
   **margin** that admins configure **per brand** (and separately for the "Alabama"
   branch, which has its own margin rules).
3. **Admins** can log in to edit margins, hide brands, override cost prices, and view a
   price-edit history.
4. **Sync** pulls fresh stock quantities and cost prices from the ERP API on a schedule,
   while **preserving** admin-edited prices.
5. **Live updates**: when a sync finishes, connected browsers get an SSE push so stock
   pages refresh without a manual reload.
6. **Device access control**: non-approved devices are restricted; admins approve
   devices from an admin page.

### Branches / Warehouses
There are two conceptual groupings:

- **Wholesale / main DBs** (`DB_PATHS` in `app.py`):
  - `DIP` → `stock_data_headoffice.db`
  - `RASALKHORE` → `stock_data_rasalkhor.db`
  - `ALABAMA` → `stock_data_alabama.db`
- **Retail branches** are *columns* inside the DIP database (not separate DB files):
  `AJMAN, NAH, DEIRA, DEIRA2, ABUDHABI, QUSAIS` (plus a combined `ALLSTORES` view).

### Warehouse → Branch mapping (from the API)
`WAREHOUSE_MAPPING` in `app.py` maps ERP warehouse codes to a branch + column:

| Code | Branch | Column |
|------|--------|--------|
| 01 | DIP | Stock Quantity |
| 02 | DIP | AJMAN |
| 03 | DIP | NAH |
| 04 | DIP | DEIRA |
| 05 | DIP | DEIRA2 |
| 06 | DIP | QUSAIS |
| 07 | DIP | ABUDHABI |
| 08 | RASALKHORE | Stock Quantity |

(Note: 8 warehouses total; Alabama is synced/managed separately.)

---

## 3. Directory & File Guide

### Root — code
| Path | Purpose |
|------|---------|
| **`app.py`** | The whole Flask backend: routes, DB access, sync, pricing, SSE, auth, device control. **Start here.** |
| `app - Copy.py` | Old backup copy of the app. **Not used** — safe to ignore/delete once you're confident. |
| `manage.py` | CLI used by **cron on the VPS**: `python manage.py sync_all` runs a full stock sync from the API and broadcasts SSE. This is the current production sync path. |
| `sync_stock_pc.py` | **Legacy** sync script that ran on the office PC on a 5-min loop. Superseded by VPS cron + `manage.py`. See `WHICH_SYNC_SCRIPT.md`. |
| `gunicorn_config.py` | Production Gunicorn config. **workers=1** (mandatory for SQLite), gthread, 8 threads, port 5000. |
| `fix_vera_duplicates.py` | One-off maintenance script to clean duplicate rows. |
| `recover_overrides.py` | One-off script to recover admin price overrides (recovery tool). |
| `fix_duplicates.sql`, `fix_duplicates_safe.sql` | SQL maintenance snippets for de-duping. |

### Root — config & ops
| Path | Purpose |
|------|---------|
| `.env` | **Secrets / runtime config** (NOT committed — in `.gitignore`). Keys: `API_BASE_HOST`, `API_TIMEOUT`, `BRAND_MARGINS_API_KEY`. |
| `.env.example` | Template for `.env`. Copy it to `.env` and fill in values. |
| `requirements.txt` | Python dependencies (Flask, pandas, openpyxl, requests, schedule, python-dotenv, etc.). |
| `nginx_stockweb.conf` | Nginx site config (reverse proxy → gunicorn, SSE tuning). |
| `backup_dbs.sh` | Shell script to back up the `.db` files. |
| `start_sync_service.bat` / `stop_sync_service.bat` | Windows batch files to start/stop the **legacy** PC sync service (`pythonw sync_stock_pc.py`). |
| `stockandso.bat` | Windows helper describing the **current** VPS-sync mode (reminds you to start the SSH tunnel). |
| `.vscode/`, `.claude/` | Editor / assistant settings. Non-runtime. |
| `LICENSE` | License file. |

### Root — databases (all git-ignored, `*.db`)
| Path | Purpose |
|------|---------|
| `stock_data_headoffice.db` | **DIP** branch + all retail-branch columns. The primary DB. |
| `stock_data_rasalkhor.db` | RASALKHORE branch stock. |
| `stock_data_alabama.db` | ALABAMA branch stock (separate margin rules). |
| `stock_data_alabama.db.bak` | Backup of the Alabama DB. |
| `devices.db` | `trusted_devices` table for device-approval access control. |

> These DBs are **the real data** and are **not** in git. Back them up (`backup_dbs.sh`)
> before any risky change. WAL mode is enabled, so you may also see `*.db-wal` /
> `*.db-shm` sidecar files at runtime.

### `templates/` — Jinja2 HTML
| File | Purpose |
|------|---------|
| `home.html`, `home-back.html`, `homegsap.html`, `index.html` | Landing / home page variants. `home.html` is the active one; the others are older versions. |
| `alabama_home.html` | Alabama landing page. |
| `stock.html`, `_stock_results.html`, `stock - Copy.html` | Main stock search page + its results partial. `- Copy` is an old backup. |
| `item_detail.html` | Single-item detail view. |
| `upload.html` | Excel upload / manual "sync from API" trigger page. |
| `login.html` | Admin/staff login. |
| `register_device.html`, `device_pending.html` | Device registration + "pending approval" screens. |
| **Admin pages:** `admin_devices.html` | Approve/deny trusted devices. |
| `admin_brand_margins.html` | Set per-brand margins (main). |
| `admin_alabama_margins.html` | Alabama-specific margins. |
| `admin_hidden_brands.html` | Hide/show brands. |
| `admin_cost_price_overrides.html` | Manually override cost prices. |
| `admin_price_edit_history.html` | Audit log of price edits. |

### `static/`
| File | Purpose |
|------|---------|
| `static/css/shared.css` | Shared stylesheet used across templates. |

### `uploads/`
| File | Purpose |
|------|---------|
| `uploads/stock_details.xlsx` | Last uploaded Excel file (upload target dir). `*.xlsx` is git-ignored. |

### `logs/` & log files
| Path | Purpose |
|------|---------|
| `logs/sync_vps.log` | Rotating log written by `manage.py` (VPS cron sync). 10 MB × 5 backups. |
| `sync_stock.log` | Log from the **legacy** PC sync script. |

### Documentation (`*.md` / `*.txt`) — read as needed
There are many topic-specific docs from the app's history. Most useful:

| Doc | Topic |
|-----|-------|
| `SYNC_WORKFLOW.md`, `WHICH_SYNC_SCRIPT.md`, `TEST_SYNC.md` | How syncing works and which script to use. **Read these early.** |
| `BACKGROUND_SYNC_GUIDE.md`, `SYNC_LOGS_GUIDE.md` | Background sync + reading logs. |
| `REALTIME_SSE_WORKFLOW.md`, `SSE_CHANGES_SUMMARY.md` | How the live-update (SSE) system works. |
| `PRODUCTION_DEPLOYMENT.md`, `VPS_DEPLOYMENT.md`, `VPS_SYNC_DEPLOY.md` | Deploying to the VPS. |
| `NGINX_SSE_CONFIG.md`, `NGINX_MAIN_CONF_FIX.md`, `NGINX_CONFIG_UPDATED.txt`, `APACHE_SSE_CONFIG.md` | Web-server config for SSE. |
| `DATABASE_LOCK_FIX.md`, `PERFORMANCE_FIXES.md`, `PERFORMANCE_OPTIMIZATIONS.md`, `LOGIC_VERIFICATION.md` | SQLite locking + performance history. |
| `ALABAMA_ENHANCEMENTS.md`, `FREE_STOCK_IMPLEMENTATION_PLAN.md`, `FIXES_SSE_AND_BRAND_MARGIN.md` | Feature-specific notes. |
| `TESTING_LOCAL.md` | Running/testing locally. |
| `PRE_PUSH_CHECKLIST.md`, `PRODUCTION_CHANGES_QUICK.md`, `UI_CHANGES_SUMMARY.md` | Process/checklists. |
| `{.md` | Junk filename (created by accident). Safe to delete. |

---

## 4. Architecture Overview

```
                         ┌──────────────────────────────┐
   Office PC             │            VPS (Linux)        │
 ┌───────────┐  SSH -R   │  ┌────────┐   ┌────────────┐  │
 │ ERP API   │◄──tunnel──┼──│ cron   │──►│ manage.py  │  │
 │192.168.x  │  :8443    │  │ (sync) │   │ sync_all   │  │
 └───────────┘           │  └────────┘   └─────┬──────┘  │
                         │                     │ writes  │
                         │              ┌──────▼───────┐  │
   Browser ──HTTP──►Nginx├──►Gunicorn──►│  app.py      │  │
   (SSE) ◄──push──────── │   (1 worker) │  + SQLite DBs│  │
                         │              └──────────────┘  │
                         └──────────────────────────────┘
```

- **Sync path (production):** VPS cron runs `python manage.py sync_all` → calls
  `sync_all_warehouses_from_api()` in `app.py` → fetches each warehouse from the ERP
  API (reached at `localhost:8443` because the office PC holds an SSH reverse tunnel to
  the ERP) → writes stock qty + cost price into the SQLite DBs (preserving admin prices)
  → `broadcast_sse_update()` pushes a refresh to connected browsers.
- **Serve path:** Nginx → Gunicorn (1 worker / 8 threads) → Flask routes render templates.
- **SSE:** `/api/stock-stream/<branch>` keeps a connection open; `sse_connections` (an
  in-memory dict guarded by a lock) holds per-branch queues.

---

## 5. `app.py` Map (where to find things)

Search for these functions/sections (line numbers approximate):

**Infrastructure**
- `get_db_connection()` — SQLite connection helper: **WAL mode**, `busy_timeout`,
  retry-with-backoff on "database is locked". Use this for all DB access.
- `init_device_db()` — creates `trusted_devices` table on startup.
- SSE: `get_sse_queue()`, `broadcast_sse_update()`, `sse_connections`, `sse_lock`.
- `handle_500()` — friendly message for DB-lock errors, full traceback otherwise.
- Device control: `_check_device_token()`, `device_restriction_middleware()`.

**Config (top of file)**
- `app.secret_key`, `USERS` (hardcoded `admin`/`staff`, hashed) — see Security below.
- `DB_PATHS`, `RETAIL_BRANCHES`, `HIDDEN_BRANDS`, `WAREHOUSE_MAPPING`.
- `API_BASE_URL` / `API_TIMEOUT` — resolved from `.env` (`API_BASE_HOST` →
  `.../IntegrationApi/api/Stock`, fallback `http://192.168.1.103/...`).

**Pricing logic** (the heart of the business rules)
- `ensure_brand_margins_table()`, `get_brand_margin()`, `get_default_margin()`.
- `ensure_alabama_margins_table()`, `get_alabama_margins()`.
- `selling_price_from_cost_and_markup_margin()` — cost + margin → selling price.
- `apply_admin_extra_margin()`.
- Cost overrides: `ensure_cost_price_overrides_table()`, `get_cost_price_overrides()`.
- Hidden brands: `ensure_hidden_brands_override_table()`, `get_effective_hidden_brands()`.

**Sync & upload**
- `sync_stock_from_api(warehouse_code)`, `sync_all_warehouses_from_api()`.
- `cleanup_sync_remove_stale_items()`, `_fetch_item_codes_from_api()`.
- `process_excel()`, `update_database()`, `upload_file()` (`/uploadstock`).
- `ensure_stock_items_columns/indexes()`, `ensure_alabama_stock_items_table()`.

**Routes** (grep `@app.route`)
- Public/branch: `/`, `/a`, `/headoffice`, `/rasalkhor`, `/alabama`, and retail
  routes `/ajman /nah /deira /deira2 /abudhabi /qusais /allstores` (all funnel into
  `stock_page()` / `retail_page()`).
- Item: `/item/<branch>/<item_code>`.
- Auth: `/login`, `/logout`.
- APIs: `/api/stock`, `/api/min-price`, `/api/sync-stock`, `/api/stock-stream/<branch>`,
  `/api/notify-sync-complete`, `/api/brand-margin(s)`, `/api/alabama-margin`, etc.
- Admin: `/admin/devices`, `/admin/brand-margins`, `/admin/alabama-margins`,
  `/admin/hidden-brands`, `/admin/cost-price-overrides`, `/admin/price-edit-history`,
  `/admin/cleanup-sync`.
- Device: `/register-device`, `/device-pending`.

---

## 6. Local Setup (Development)

```bash
# 1. Python venv
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac

# 2. Dependencies
pip install -r requirements.txt
#   NOTE: app.py imports flask_cors but it is NOT in requirements.txt.
#         If you hit "No module named flask_cors": pip install flask-cors

# 3. Config
cp .env.example .env           # then edit values (API host, timeout)

# 4. Databases
#   The .db files are NOT in git. Get copies from the current server / previous
#   maintainer and place them in the project root (names must match DB_PATHS).

# 5. Run
python app.py                  # http://localhost:5000  (debug=True)
```

Default logins (⚠️ change these — see Security): `admin` / `junaid6231`, `staff` / `staff123`.

See `TESTING_LOCAL.md` for more.

---

## 7. Production Deployment (VPS) — high level

1. Code on the VPS, `.env` filled in, `.db` files present.
2. **Gunicorn** runs the app: `gunicorn -c gunicorn_config.py app:app`
   (binds `0.0.0.0:5000`, **1 worker** — do not increase; SQLite will lock).
3. **Nginx** reverse-proxies to Gunicorn and is tuned for SSE (buffering off).
   See `nginx_stockweb.conf` + `NGINX_SSE_CONFIG.md`.
4. **Office PC** must keep the SSH reverse tunnel up so the VPS can reach the ERP API:
   `ssh -N -R 8443:192.168.1.103:80 user@VPS_IP` (see `stockandso.bat`).
5. **Cron** on the VPS runs the sync: `python manage.py sync_all` (logs to
   `logs/sync_vps.log`).

Full details: `PRODUCTION_DEPLOYMENT.md`, `VPS_DEPLOYMENT.md`, `VPS_SYNC_DEPLOY.md`.

---

## 8. Critical Gotchas (read before changing anything)

1. **SQLite + single worker.** `gunicorn_config.py` uses `workers=1` on purpose. More
   than one process on the same `.db` = "database is locked" errors in production.
   Concurrency comes from **threads**, not workers.
2. **WAL mode & retries.** Always go through `get_db_connection()`. It sets WAL, a busy
   timeout, and retries on lock. Raw `sqlite3.connect()` calls exist in some routes
   (e.g. `retail_page`) — be careful when adding new ones.
3. **Admin prices must be preserved on sync.** Sync functions take
   `keep_admin_prices=True`. If you break this, admin-edited prices get overwritten by
   ERP cost prices. Verify against `LOGIC_VERIFICATION.md`.
4. **Selling price is computed, not stored.** Changing margin logic changes every
   displayed price. Test with `admin_price_edit_history` and known items.
5. **Alabama is special.** It has its own DB and its own margin table/logic — update
   both the general and the Alabama paths when touching pricing.
6. **The DBs are not in git.** Never assume a fresh clone can run. Back them up before
   migrations (`backup_dbs.sh`).
7. **Two sync scripts exist.** Only `manage.py` (VPS cron) is current. `sync_stock_pc.py`
   is legacy. Don't run both against the same data.
8. **SSE needs the right web-server config.** Proxy buffering must be off, long timeouts.
   If live updates "don't work," suspect Nginx/Apache config first.

---

## 9. Security TODOs (inherited — flagged for the new owner)

These are **hardcoded in the code today** and should be fixed:

- `app.secret_key = "junaid2365"` is hardcoded in `app.py` → move to `.env`.
- `USERS` dict has **hardcoded credentials** (`admin`/`junaid6231`, `staff`/`staff123`)
  → move to DB or env, and change the passwords.
- `debug=True` in `app.py`'s `app.run()` → must be **off** in production (Gunicorn
  doesn't use `app.run()`, but don't rely on that).
- `BRAND_MARGINS_API_KEY` lives in `.env` (good) — make sure `.env` is never committed
  (it's already git-ignored).

---

## 10. First-Week Checklist for the New Developer

1. [ ] Get the `.db` files and a valid `.env` from the current maintainer.
2. [ ] Run locally (`python app.py`), log in as admin, search a branch, open an item.
3. [ ] Read `SYNC_WORKFLOW.md` + `WHICH_SYNC_SCRIPT.md`, then trace `manage.py
       sync_all` → `sync_all_warehouses_from_api()`.
4. [ ] Read `REALTIME_SSE_WORKFLOW.md` and watch an SSE stream in the browser dev tools.
5. [ ] Understand the pricing chain: cost → brand margin → admin extra margin →
       selling price (functions listed in §5).
6. [ ] Skim the admin pages (margins, hidden brands, cost overrides, price history).
7. [ ] Note the SSH tunnel + VPS cron dependency for sync.
8. [ ] Address the security TODOs in §9.

---

## 11. Key Contacts / Where State Lives (fill in)

- **VPS host / SSH access:** _(ask current maintainer)_
- **ERP API owner / credentials:** _(ask current maintainer)_
- **Who holds the SSH-tunnel PC:** _(ask current maintainer)_
- **Backups location:** produced by `backup_dbs.sh` → _(confirm destination)_

---

*Generated as a handover reference. When code changes, keep §3, §5 and §8 up to date —
they are the parts a newcomer relies on most.*
