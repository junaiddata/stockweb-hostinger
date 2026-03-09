from flask import Flask, request, render_template, redirect, url_for
import sqlite3
import pandas as pd
import os
from flask import Flask, request, render_template, redirect, url_for, session, flash, Response

# Load .env for API_BASE_URL, API_TIMEOUT (VPS sync via tunnel)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from werkzeug.security import check_password_hash, generate_password_hash
import requests
from datetime import datetime
import uuid
import threading
import queue
import json
import time
from contextlib import contextmanager


DEVICE_DB = "devices.db"

# Database connection helper with timeout and WAL mode
@contextmanager
def get_db_connection(db_path: str, timeout: float = 10.0, retries: int = 3):
    """
    Get SQLite database connection with timeout and retry logic.
    Enables WAL mode for better concurrent access.
    """
    conn = None
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(db_path, timeout=timeout)
            # Enable WAL mode for concurrent reads (critical for VPS with sync)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")  # 5 second wait - fail fast, avoid 60s LCP when locked
            yield conn
            conn.commit()
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < retries - 1:
                wait_time = 0.1 * (2 ** attempt)  # Exponential backoff
                time.sleep(wait_time)
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
                continue
            else:
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
                raise
        except Exception:
            if conn:
                try:
                    conn.close()
                except:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

def init_device_db():
    with get_db_connection(DEVICE_DB) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS trusted_devices (
                token TEXT PRIMARY KEY,
                device_name TEXT,
                ip_address TEXT,
                status TEXT DEFAULT 'pending', -- 'pending' or 'approved'
                created_at TEXT
            )
        ''')


# Initialize it on startup
init_device_db()

app = Flask(__name__)

# ============================================================================
# SSE (Server-Sent Events) for Real-Time Updates
# ============================================================================
# Store active SSE connections per branch
sse_connections = {}
sse_lock = threading.Lock()

def get_sse_queue(branch):
    """Get or create SSE message queue for a branch."""
    with sse_lock:
        if branch not in sse_connections:
            sse_connections[branch] = []
        return sse_connections[branch]

@app.errorhandler(500)
def handle_500(error):
    """Return user-friendly message for database lock / server errors."""
    import traceback
    import sys
    tb = traceback.format_exc()
    # Log to stderr so gunicorn captures it (print may not reach logs on VPS)
    sys.stderr.write(f"[500 ERROR]\n{tb}\n")
    sys.stderr.flush()
    # Check if database-related (Flask/WSGI may wrap the exception)
    exc = getattr(error, 'original_exception', error)
    msg = (str(exc) + tb).lower()
    is_db_error = (
        isinstance(exc, sqlite3.OperationalError) or
        'locked' in msg or
        'database' in msg or
        'sqlite' in msg or
        'operationalerror' in msg or
        'busy' in msg
    )
    friendly = '''
    <html><head><meta charset="utf-8"><title>Please Try Again</title></head>
    <body style="font-family:sans-serif;text-align:center;padding:60px;background:#f5f5f5;">
    <h2 style="color:#d32f2f;">Database is temporarily busy</h2>
    <p>The server is processing a data sync. Please <a href="javascript:location.reload()">refresh the page</a> in a few seconds.</p>
    <p style="color:#666;font-size:14px;">If this continues, try again in 1-2 minutes.</p>
    </body></html>
    '''
    if is_db_error:
        return friendly, 503
    # Also show friendly message for any 500 - likely sync-related on VPS
    return friendly, 503

def broadcast_sse_update(branch, data):
    """Broadcast update to all SSE connections for a branch."""
    with sse_lock:
        if branch in sse_connections:
            # Remove closed connections (check if queue is still valid)
            active_queues = []
            for q in sse_connections[branch]:
                try:
                    # Try to put message (non-blocking)
                    q.put_nowait(data)
                    active_queues.append(q)
                except queue.Full:
                    # Queue is full, keep it
                    active_queues.append(q)
                except:
                    # Queue is closed/invalid, skip it
                    pass
            sse_connections[branch] = active_queues


# In-memory cache for approved device tokens (avoids DB hit on every request)
_device_token_cache = {}
_DEVICE_CACHE_TTL = 300  # 5 minutes

def _check_device_token(token):
    """Check device token status, using in-memory cache to skip DB queries."""
    now = time.time()
    cached = _device_token_cache.get(token)
    if cached and (now - cached["ts"]) < _DEVICE_CACHE_TTL:
        return cached["status"]
    try:
        with get_db_connection(DEVICE_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM trusted_devices WHERE token = ?", (token,))
            row = c.fetchone()
        status = row[0] if row else None
    except sqlite3.OperationalError:
        status = _device_token_cache.get(token, {}).get("status")
    _device_token_cache[token] = {"status": status, "ts": now}
    return status

@app.before_request
def device_restriction_middleware():
    if request.path.startswith('/static'):
        return

    if request.path.startswith('/api/stock-stream/'):
        return
    
    if request.path == '/api/notify-sync-complete':
        return

    allowed_endpoints = [
        'login', 'register_device', 'device_pending', 'approve_devices',
        'admin_brand_margins', 'api_update_brand_margin', 'logout',
        'logo_proxy', 'stock_api', 'api_sync_stock', 'api_get_brand_margins'
    ]
    
    if request.endpoint in allowed_endpoints:
        return

    token = request.cookies.get('device_token')
    
    if not token:
        return redirect(url_for('register_device'))

    status = _check_device_token(token)

    if not status:
        return redirect(url_for('register_device'))
    
    if status != 'approved':
        return redirect(url_for('device_pending'))

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"xls", "xlsx"}
app.secret_key = "junaid2365"  # Required for session cookies

# Example: hardcoded users (can be moved to DB)
USERS = {
    "admin": generate_password_hash("junaid6231"),  # Hashed password
    "staff": generate_password_hash("staff123")
}



# Define the SQLite database file path (absolute paths for VPS - avoids CWD issues)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATHS = {
    "DIP": os.path.join(_BASE_DIR, "stock_data_headoffice.db"),
    "RASALKHORE": os.path.join(_BASE_DIR, "stock_data_rasalkhor.db"),
    "ALABAMA": os.path.join(_BASE_DIR, "stock_data_alabama.db")
}

# Retail branch names exactly as your OUTPUT_DIP column headers
RETAIL_BRANCHES = ["AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS","ALLSTORES"]

# Brands to hide from Alabama and Junaid Brand Margins admin pages (old/legacy brands)
HIDDEN_BRANDS = frozenset(b.strip().upper() for b in """
AA JUMA
ALLFLEX
ALMANIT
ALPINE
AQUA
AQUA  ECO
AQUA GAS
AQUAPLAST
AQUATHERM
AQUAWELD
ARAMIX
ARMITAGE SHANKS
ASB
ASCON
ASHIRWAD
BBSPAIN
BENNINGER
BESTWELD
BIS
BLANCO
BORZ
BOSSINI
BRADFORD
BUGATI
CLEVER
COCO BELLA
CONCEPT
CONCORD
CRANE
CRI
CTESI
DELABIE
DIAMOND
EFFEPI
ELDOM
ELECTRIC
ELECTRIC ALI SHAHDAD
ELOFIT
ENDEX
ENOLGAS
ESBE
ESWIT
EURO
EVERSAFE/PROJECT
EXCEL
F.MORI
FARIS
FERROLI
FLOWCON
FLOWTECH-MPI
FORMEC
FRANKLYN
FRASCIO
GALA
GEBRIT
GIACOMINI-ITALY
GRANDFOSE
GROHE OLD
HAKAN
HAMADA
HARDWARE
HEATEX
HERZ
HIMARK
IDEAL
IG
ITAP
ITIPLAST
J.K.CERA INTERNATIONAL
JAGUAR
JEVCO
JOCKEY
JUNE-WATER HEATER
KALPADA
LOCAL
LOWARA
LT
LUBI
MACDEE
MARINA
MBEE
MCALPINE
MILANO
MILIN TUBES
MIRAGE
MUELLER EUROPE
MUH-ASCON
MUH-EBRAR
MUH-FEDCAB
MUH-FRANKLIN
MUH-MBEE
MUH-OSWAL
MUH-SAER
MUH-SPERONI
MUH-STARTER
MUH-VARUNA
MUH-XTRACAB
MULTIFLOW
MURI SILIENT PIPE
MARAZZI
NATIONAL
NIAGARA
NOVATHERM
OLD STOCK
OMEGA
ORIENT
PALSON
PATTEX
PEGLER- OLD
PEGLER-XPRESS
PENTAGONO
PILOT
PILSA
PLUMBING
POLO
POLYPIPE
POWER
RASTELI
REFERENCIA
REWT
S KRIPA
SAER
SANITARYWARE
SANWA
SARIA
SAUDI
SAXON
SHAKTHI
SKOLAN
SMITH
SPERONI
STATE
SUNDEX
SUNNEX
SUSPECT
SWEDE
SWME
THERMEX
THERMOWATT
TILES
TM-OLD
TOPI
TRADEX
TSP
ULTRA
ULTRAFLOW
UNIFLO
VALTECH
VALVEIT
VEIGA
VELENCIA
VENICE
VESBO
VESPA
WATER TECH
WATERFORCE
WEFATHERM
WINNER
WIRQUIN
ZENITH GI PIPE
""".strip().splitlines() if b.strip())


def ensure_retail_override_table(db_path: str):
    """
    Overrides for retail branches live only in the DIP DB.
    Keyed by (ItemCode, Branch). Does NOT affect existing price_overrides tables.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS retail_overrides (
                        ItemCode TEXT,
                        Branch   TEXT,
                        SellingPriceOverride REAL,
                        edited_by TEXT,
                        edited_at TEXT DEFAULT (datetime('now')),
                        PRIMARY KEY (ItemCode, Branch)
                    )
                """)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
                continue
            else:
                raise

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Default margin percentage for all brands (can be changed by admin)
DEFAULT_MARGIN_PERCENT = 15.0

def ensure_brand_margins_table(db_path: str):
    """
    Create brand_margins table for storing margin percentages per brand/manufacturer.
    Also stores the default margin setting.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS brand_margins (
                        brand_name TEXT PRIMARY KEY,
                        margin_percent REAL DEFAULT 15.0,
                        use_admin_price INTEGER DEFAULT 1,
                        edited_by TEXT,
                        edited_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                # Add use_admin_price if missing (migration)
                cols = [r[1] for r in cur.execute("PRAGMA table_info(brand_margins)").fetchall()]
                if "use_admin_price" not in cols:
                    try:
                        cur.execute("ALTER TABLE brand_margins ADD COLUMN use_admin_price INTEGER DEFAULT 1")
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
                    cur.execute("UPDATE brand_margins SET use_admin_price = 1 WHERE use_admin_price IS NULL")
                # Insert default margin row if not exists
                cur.execute("""
                    INSERT OR IGNORE INTO brand_margins (brand_name, margin_percent, use_admin_price, edited_by)
                    VALUES ('__DEFAULT__', 15.0, 1, 'system')
                """)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
                continue
            else:
                raise

def ensure_alabama_margins_table(db_path: str):
    """
    Create alabama_margins table for storing Alabama-specific margins.
    Two types of margins:
    1. cost_margin_percent: Applied to Junaid Cost → Alabama Cost (additive: Cost * (1 + margin/100))
    2. brand_margin_percent: Applied to Alabama Cost → Alabama Selling Price (division: Cost / (1 - margin/100))
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS alabama_margins (
                        brand_name TEXT PRIMARY KEY,
                        cost_margin_percent REAL DEFAULT 10.0,  -- Default 10% markup on Junaid cost
                        brand_margin_percent REAL DEFAULT 15.0,  -- Default 15% margin for selling price
                        edited_by TEXT,
                        edited_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                # Insert default margin row if not exists
                cur.execute("""
                    INSERT OR IGNORE INTO alabama_margins (brand_name, cost_margin_percent, brand_margin_percent, edited_by)
                    VALUES ('__DEFAULT__', 10.0, 15.0, 'system')
                """)
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
                continue
            raise

def get_alabama_margins(db_path: str, brand_name: str) -> tuple:
    """
    Get Alabama margins for a specific brand.
    Returns: (cost_margin_percent, brand_margin_percent)
    Falls back to defaults if brand not found.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Try to get brand-specific margins
    cur.execute("SELECT cost_margin_percent, brand_margin_percent FROM alabama_margins WHERE brand_name = ?", (brand_name,))
    row = cur.fetchone()
    
    if row:
        conn.close()
        return (row[0], row[1])
    
    # Fall back to default margins
    cur.execute("SELECT cost_margin_percent, brand_margin_percent FROM alabama_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    conn.close()
    
    if row:
        return (row[0], row[1])
    else:
        return (10.0, 15.0)  # Hardcoded defaults if table doesn't exist

def get_brand_margin(db_path: str, brand_name: str) -> float:
    """
    Get margin percentage for a specific brand.
    Falls back to default if brand not found.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Try to get brand-specific margin
    cur.execute("SELECT margin_percent FROM brand_margins WHERE brand_name = ?", (brand_name,))
    row = cur.fetchone()
    
    if row:
        conn.close()
        return row[0]
    
    # Fall back to default margin
    cur.execute("SELECT margin_percent FROM brand_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    conn.close()
    
    return row[0] if row else DEFAULT_MARGIN_PERCENT

def get_default_margin(db_path: str) -> float:
    """Get the default margin percentage."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT margin_percent FROM brand_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_MARGIN_PERCENT


# add anywhere near your helpers
def fetch_sold_map():
    """
    Returns: dict { "ITEMCODE": total_qty_sold }
    Pulls from https://salesorder.junaidworld.com/api/item-analysis-totals/
    """
    url = "https://salesorder.junaidworld.com/api/item-analysis-totals/"
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json() or {}
        sold = {}
        for row in (data.get("results") or []):
            code = str(row.get("item_code", "")).strip()
            qty  = row.get("total_qty", 0)
            try:
                qty = float(qty)
            except Exception:
                qty = 0
            if code:
                sold[code] = qty
        return sold
    except Exception as e:
        # Don't break the page if API is down
        print("Sold API error:", e)
        return {}
    

def _to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

# Cache for sold breakdown map (5 minute TTL)
_sold_map_cache = {}
_sold_map_cache_time = None
SOLD_MAP_CACHE_TTL = 300  # 5 minutes

def fetch_sold_breakdown_map():
    """
    Returns: { "ITEMCODE": {"total": float, "ho": float, "others": float, "total_2025": float, "total_2026": float}, ... }
    Pulls from your new /api/items/unique-qty endpoint.
    OPTIMIZATION: Cached for 5 minutes to reduce API calls and prevent timeouts.
    """
    global _sold_map_cache, _sold_map_cache_time
    
    # Check cache
    import time
    current_time = time.time()
    if _sold_map_cache_time and (current_time - _sold_map_cache_time) < SOLD_MAP_CACHE_TTL:
        return _sold_map_cache
    
    url = "https://salesorder.junaidworld.com/api/item-analysis-totals/"
    try:
        # OPTIMIZATION: Reduced timeout to 2s, fail fast to prevent blocking
        r = requests.get(url, timeout=2)
        r.raise_for_status()
        data = r.json() or {}
        out = {}
        for row in (data.get("results") or []):
            code = str(row.get("item_code", "")).strip()
            if not code:
                continue
            def f(x): 
                try: return float(x)
                except Exception: return 0.0
            out[code] = {
                "total":  f(row.get("total_qty", 0)),
                "ho":     f(row.get("ho_qty", 0)),
                "others": f(row.get("others_qty", 0)),
                "total_2025": f(row.get("total_2025", 0)),
                "total_2026": f(row.get("total_2026", 0)),
            }
        # Update cache
        _sold_map_cache = out
        _sold_map_cache_time = current_time
        return out
    except requests.Timeout:
        print("Sold breakdown API timeout (using cache if available)")
        # Return cached data even if expired, better than nothing
        return _sold_map_cache if _sold_map_cache else {}
    except Exception as e:
        print(f"Sold breakdown API error (using cache if available): {e}")
        # Return cached data even if expired, better than nothing
        return _sold_map_cache if _sold_map_cache else {}
    
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# Warehouse mapping: API warehouse codes to database branches/columns
WAREHOUSE_MAPPING = {
    "01": {"branch": "DIP", "column": "Stock Quantity"},
    "02": {"branch": "DIP", "column": "AJMAN"},
    "03": {"branch": "DIP", "column": "NAH"},
    "04": {"branch": "DIP", "column": "DEIRA"},
    "05": {"branch": "DIP", "column": "DEIRA2"},
    "06": {"branch": "DIP", "column": "QUSAIS"},
    "07": {"branch": "DIP", "column": "ABUDHABI"},
    "08": {"branch": "RASALKHORE", "column": "Stock Quantity"},
}

# API URL for stock sync: from env (VPS uses localhost:8443 via SSH tunnel) or fallback
_api_host = os.environ.get("API_BASE_HOST", "").rstrip("/")
_api_url = os.environ.get("API_BASE_URL", "")
if _api_url:
    API_BASE_URL = _api_url
elif _api_host:
    API_BASE_URL = f"{_api_host}/IntegrationApi/api/Stock"
else:
    API_BASE_URL = "http://192.168.1.103/IntegrationApi/api/Stock"
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "60"))

def sync_stock_from_api(warehouse_code, keep_admin_prices=True):
    """
    Fetch stock data from API for a specific warehouse and update the database.
    
    Args:
        warehouse_code: String warehouse code ("01" to "08")
        keep_admin_prices: If True, preserve existing admin-edited prices
    
    Returns:
        tuple: (success: bool, items_updated: int, error_message: str)
    """
    if warehouse_code not in WAREHOUSE_MAPPING:
        return False, 0, f"Invalid warehouse code: {warehouse_code}"
    
    mapping = WAREHOUSE_MAPPING[warehouse_code]
    branch = mapping["branch"]
    stock_column = mapping["column"]
    
    try:
        # Call API (VPS reaches via localhost:8443 when SSH tunnel is active)
        payload = {"Warehouse": warehouse_code, "Active": "Y"}
        response = requests.post(API_BASE_URL, json=payload, timeout=API_TIMEOUT)
        response.raise_for_status()
        
        api_data = response.json()
        
        if not api_data or "Data" not in api_data:
            return False, 0, "Invalid API response format"
        
        items = api_data.get("Data", [])
        if not items:
            return True, 0, "No items returned from API"
        
        # Get database path
        db_path = DB_PATHS[branch]
        ensure_override_table(db_path)
        if branch == "DIP":
            ensure_retail_override_table(db_path)
        
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Get existing admin price overrides before updating
        existing_overrides = {}
        existing_retail_overrides = {}
        
        if keep_admin_prices:
            # Get price_overrides (for DIP and RASALKHORE SellingPriceOverride)
            cur.execute("SELECT ItemCode, SellingPriceOverride FROM price_overrides WHERE SellingPriceOverride IS NOT NULL")
            for row in cur.fetchall():
                existing_overrides[row[0]] = row[1]
            
            # Get retail_overrides (for retail branches if updating DIP)
            if branch == "DIP":
                cur.execute("SELECT ItemCode, Branch, SellingPriceOverride FROM retail_overrides WHERE SellingPriceOverride IS NOT NULL")
                for row in cur.fetchall():
                    item_code, retail_branch, price = row
                    if item_code not in existing_retail_overrides:
                        existing_retail_overrides[item_code] = {}
                    existing_retail_overrides[item_code][retail_branch] = price
        
        # Check if stock_items table exists and get current structure
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
        table_exists = cur.fetchone() is not None
        
        if not table_exists:
            # Create table with proper structure based on branch
            if branch == "DIP":
                columns_sql = '''
                    CREATE TABLE stock_items (
                        "ItemCode" TEXT PRIMARY KEY,
                        "Upc Code" TEXT,
                        "Description" TEXT,
                        "Manufacturer Name" TEXT,
                        "Warehouse Code" TEXT,
                        "Stock Quantity" REAL DEFAULT 0,
                        "Free Stock" REAL DEFAULT 0,
                        "Selling Price" REAL DEFAULT 0,
                        "CostPrice" REAL DEFAULT 0,
                        "AJMAN" REAL DEFAULT 0,
                        "NAH" REAL DEFAULT 0,
                        "DEIRA" REAL DEFAULT 0,
                        "DEIRA2" REAL DEFAULT 0,
                        "ABUDHABI" REAL DEFAULT 0,
                        "QUSAIS" REAL DEFAULT 0
                    )
                '''
            else:
                columns_sql = '''
                    CREATE TABLE stock_items (
                        "ItemCode" TEXT PRIMARY KEY,
                        "Upc Code" TEXT,
                        "Description" TEXT,
                        "Manufacturer Name" TEXT,
                        "Warehouse Code" TEXT,
                        "Stock Quantity" REAL DEFAULT 0,
                        "Free Stock" REAL DEFAULT 0,
                        "Selling Price" REAL DEFAULT 0,
                        "CostPrice" REAL DEFAULT 0
                    )
                '''
            cur.execute(columns_sql)
            conn.commit()
            # Create indexes after table creation
            ensure_stock_items_indexes(db_path)
        
        # Get existing items to merge with (for DIP, we need to preserve other columns)
        existing_items = {}
        if branch == "DIP":
            cur.execute('SELECT "ItemCode", "AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS", "Stock Quantity", "Selling Price", "CostPrice", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code", "Free Stock" FROM stock_items')
            for row in cur.fetchall():
                existing_items[row[0]] = {
                    "AJMAN": row[1] or 0,
                    "NAH": row[2] or 0,
                    "DEIRA": row[3] or 0,
                    "DEIRA2": row[4] or 0,
                    "ABUDHABI": row[5] or 0,
                    "QUSAIS": row[6] or 0,
                    "Stock Quantity": row[7] or 0,
                    "Selling Price": row[8] or 0,
                    "CostPrice": row[9] or 0,
                    "Upc Code": row[10] or "",
                    "Description": row[11] or "",
                    "Manufacturer Name": row[12] or "",
                    "Warehouse Code": row[13] or "",
                    "Free Stock": row[14] or 0,
                }
        
        # Load brand margins and use_admin_price for DIP/RASALKHORE
        dip_db = DB_PATHS["DIP"]
        ensure_brand_margins_table(dip_db)
        brand_margins = {}
        brand_margins_lower = {}
        brand_use_admin = {}
        brand_use_admin_lower = {}
        default_margin = DEFAULT_MARGIN_PERCENT
        use_admin_default = True
        if branch in ("DIP", "RASALKHORE"):
            with get_db_connection(dip_db, timeout=5.0) as mconn:
                mcur = mconn.cursor()
                mcur.execute("SELECT brand_name, margin_percent, COALESCE(use_admin_price, 1) FROM brand_margins")
                for row in mcur.fetchall():
                    if row[0] == "__DEFAULT__":
                        default_margin = row[1]
                        use_admin_default = bool(row[2])
                    else:
                        brand_margins[row[0]] = row[1]
                        brand_margins_lower[row[0].lower()] = (row[0], row[1])
                        brand_use_admin[row[0]] = bool(row[2])
                        brand_use_admin_lower[row[0].lower()] = bool(row[2])
        
        def _get_margin(mfg):
            if not mfg: return default_margin
            if mfg in brand_margins: return brand_margins[mfg]
            k = mfg.lower()
            return brand_margins_lower[k][1] if k in brand_margins_lower else default_margin
        def _get_use_admin(mfg):
            if not mfg: return use_admin_default
            if mfg in brand_use_admin: return brand_use_admin[mfg]
            return brand_use_admin_lower.get(mfg.lower(), use_admin_default)
        
        # Process API items
        items_to_insert = []
        items_updated = 0
        
        for item in items:
            item_code = str(item.get("ItemCode", "")).strip()
            if not item_code:
                continue
            
            # Transform API fields to database columns
            upc_code = str(item.get("U_UPCCODE", "")).strip()
            description = str(item.get("ItemName", "")).strip()
            manufacturer = str(item.get("FirmName", "")).strip()
            whs_code = str(item.get("WhsCode", "")).strip()
            on_hand = _to_float(item.get("OnHand", 0), 0.0)
            avg_price = _to_float(item.get("AvgPrice", 0), 0.0)
            
            # Selling price: use admin override if brand allows, else use brand margin
            margin_pct = _get_margin(manufacturer)
            margin_divisor = 1 - (margin_pct / 100)
            calc_price = round(avg_price / margin_divisor, 2) if avg_price > 0 and margin_divisor > 0 else 0.0
            
            if keep_admin_prices and _get_use_admin(manufacturer) and item_code in existing_overrides:
                selling_price = round(existing_overrides[item_code], 2)
            else:
                selling_price = calc_price
            
            # Build row data
            if branch == "DIP":
                # Get existing data for this item to preserve other columns
                existing = existing_items.get(item_code, {})
                
                # Use API data if available, otherwise fall back to existing data
                final_upc = upc_code if upc_code else existing.get("Upc Code", "")
                final_description = description if description else existing.get("Description", "")
                final_manufacturer = manufacturer if manufacturer else existing.get("Manufacturer Name", "")
                final_whs_code = whs_code if whs_code else existing.get("Warehouse Code", "")
                # Cost price: only update from API for Warehouse 01 (Stock Quantity). For 02-07 preserve existing (Warehouse 01) cost.
                if stock_column == "Stock Quantity":
                    final_cost_price = round(avg_price, 2)
                else:
                    existing_cost = existing.get("CostPrice", 0) or 0
                    final_cost_price = round(float(existing_cost), 2)
                
                row_data = {
                    "ItemCode": item_code,
                    "Upc Code": final_upc,
                    "Description": final_description,
                    "Manufacturer Name": final_manufacturer,
                    "Warehouse Code": final_whs_code,
                    "Stock Quantity": on_hand if stock_column == "Stock Quantity" else existing.get("Stock Quantity", 0),
                    "Free Stock": existing.get("Free Stock", 0),
                    "Selling Price": round(selling_price, 2) if selling_price > 0 else round(existing.get("Selling Price", 0), 2),
                    "CostPrice": final_cost_price,
                    "AJMAN": on_hand if stock_column == "AJMAN" else existing.get("AJMAN", 0),
                    "NAH": on_hand if stock_column == "NAH" else existing.get("NAH", 0),
                    "DEIRA": on_hand if stock_column == "DEIRA" else existing.get("DEIRA", 0),
                    "DEIRA2": on_hand if stock_column == "DEIRA2" else existing.get("DEIRA2", 0),
                    "ABUDHABI": on_hand if stock_column == "ABUDHABI" else existing.get("ABUDHABI", 0),
                    "QUSAIS": on_hand if stock_column == "QUSAIS" else existing.get("QUSAIS", 0),
                }
            else:
                # RASALKHORE branch
                row_data = {
                    "ItemCode": item_code,
                    "Upc Code": upc_code,
                    "Description": description,
                    "Manufacturer Name": manufacturer,
                    "Warehouse Code": whs_code,
                    "Stock Quantity": on_hand,
                    "Free Stock": 0,
                    "Selling Price": selling_price,
                    "CostPrice": avg_price,
                }
            
            items_to_insert.append(row_data)
            items_updated += 1
        
        # OPTIMIZATION: Use incremental updates instead of table replacement
        # This prevents database locks and improves performance
        if items_to_insert:
            # Use INSERT OR REPLACE for incremental updates (much faster than table replacement)
            if branch == "DIP":
                insert_sql = """
                    INSERT OR REPLACE INTO stock_items (
                        "ItemCode", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code",
                        "Stock Quantity", "Free Stock", "Selling Price", "CostPrice",
                        "AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS"
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                for item in items_to_insert:
                    cur.execute(insert_sql, (
                        item["ItemCode"], item["Upc Code"], item["Description"], 
                        item["Manufacturer Name"], item["Warehouse Code"],
                        item["Stock Quantity"], item["Free Stock"], 
                        item["Selling Price"], item["CostPrice"],
                        item["AJMAN"], item["NAH"], item["DEIRA"], 
                        item["DEIRA2"], item["ABUDHABI"], item["QUSAIS"]
                    ))
            else:
                insert_sql = """
                    INSERT OR REPLACE INTO stock_items (
                        "ItemCode", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code",
                        "Stock Quantity", "Free Stock", "Selling Price", "CostPrice"
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                for item in items_to_insert:
                    cur.execute(insert_sql, (
                        item["ItemCode"], item["Upc Code"], item["Description"], 
                        item["Manufacturer Name"], item["Warehouse Code"],
                        item["Stock Quantity"], item["Free Stock"], 
                        item["Selling Price"], item["CostPrice"]
                    ))
        
        conn.commit()
        conn.close()
        
        return True, items_updated, None
        
    except requests.exceptions.ConnectionError as e:
        return False, 0, f"Connection failed - check SSH tunnel is running: {str(e)}"
    except requests.exceptions.Timeout as e:
        return False, 0, f"API timeout ({API_TIMEOUT}s) - tunnel or API may be slow: {str(e)}"
    except requests.RequestException as e:
        return False, 0, f"API request failed: {str(e)}"
    except sqlite3.Error as e:
        return False, 0, f"Database error: {str(e)}"
    except Exception as e:
        return False, 0, f"Unexpected error: {str(e)}"

def sync_all_warehouses_from_api(keep_admin_prices=True):
    """
    Sync stock data from API for all warehouses (01-08).
    Uses _global_sync_lock to prevent concurrent DB writes with cleanup or PC sync.
    """
    with _global_sync_lock:
        results = {}
        for warehouse_code in sorted(WAREHOUSE_MAPPING.keys()):
            success, count, error = sync_stock_from_api(warehouse_code, keep_admin_prices)
            results[warehouse_code] = {
                "success": success,
                "items_updated": count,
                "error": error
            }
        return results


def _fetch_item_codes_from_api(warehouse_code):
    """Fetch ItemCodes from API for a warehouse. Returns set of ItemCode strings, or empty set on error."""
    try:
        payload = {"Warehouse": warehouse_code, "Active": "Y"}
        response = requests.post(API_BASE_URL, json=payload, timeout=API_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if not data or "Data" not in data:
            return set()
        items = data.get("Data", [])
        return {str(item.get("ItemCode", "")).strip() for item in items if str(item.get("ItemCode", "")).strip()}
    except Exception:
        return set()


def cleanup_sync_remove_stale_items():
    """
    Remove items from DB that are no longer in the API.
    Uses warehouse 01 as source of truth (most items are same across warehouses).
    Deletes from both DIP and RASALKHORE any items not in warehouse 01.
    Uses _global_sync_lock to prevent concurrent DB writes with sync.
    Returns (dip_deleted, ras_deleted, error_msg).
    """
    # 1. Fetch from API warehouse 01 only (primary source - most items are same across warehouses)
    wh01_codes = _fetch_item_codes_from_api("01")
    
    # 2. SAFETY: Require warehouse 01 to return data before cleanup
    if not wh01_codes:
        return 0, 0, "Warehouse 01 returned no items. Skipping cleanup to prevent accidental full delete."
    
    valid_item_codes = wh01_codes
    
    dip_deleted = 0
    ras_deleted = 0
    
    # 3. Acquire global lock - blocks sync (in-app and PC) and prevents DB lock contention
    with _global_sync_lock:
        try:
            dip_db = DB_PATHS["DIP"]
            ras_db = DB_PATHS["RASALKHORE"]
            
            def delete_stale(db_path, valid_codes):
                """Delete items not in valid_codes. Uses temp table to avoid SQLite param limit."""
                with get_db_connection(db_path, timeout=60.0) as conn:
                    cur = conn.cursor()
                    # IMMEDIATE transaction: acquire write lock upfront, avoid deadlocks
                    cur.execute("BEGIN IMMEDIATE")
                    try:
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
                        if not cur.fetchone():
                            return 0
                        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _cleanup_valid (ItemCode TEXT PRIMARY KEY)")
                        cur.execute("DELETE FROM _cleanup_valid")
                        cur.executemany("INSERT OR IGNORE INTO _cleanup_valid (ItemCode) VALUES (?)", [(c,) for c in valid_codes])
                        cur.execute('DELETE FROM stock_items WHERE "ItemCode" NOT IN (SELECT ItemCode FROM _cleanup_valid)')
                        deleted = cur.rowcount
                        cur.execute("DROP TABLE IF EXISTS _cleanup_valid")
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                return deleted
            
            dip_deleted = delete_stale(dip_db, valid_item_codes)
            ras_deleted = delete_stale(ras_db, valid_item_codes)
            
            # 4. WAL checkpoint to keep WAL file small after bulk delete
            for db_path in (dip_db, ras_db):
                try:
                    with get_db_connection(db_path, timeout=10.0) as ckpt:
                        ckpt.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
            
            return dip_deleted, ras_deleted, None
        except Exception as e:
            return dip_deleted, ras_deleted, str(e)


@app.route("/admin/cleanup-sync", methods=["POST"])
def admin_cleanup_sync():
    """Remove items from DB that are no longer in the API. Admin only."""
    global _cleanup_in_progress
    if "username" not in session:
        flash("Please login to use Cleanup Sync", "danger")
        return redirect(url_for("login"))
    
    with _sync_lock:
        if _cleanup_in_progress:
            flash("Cleanup already in progress. Please wait.", "warning")
            return redirect(url_for("upload_file"))
        if _sync_in_progress:
            flash("Sync is in progress. Please wait for it to finish before cleanup.", "warning")
            return redirect(url_for("upload_file"))
        _cleanup_in_progress = True
    
    try:
        dip_deleted, ras_deleted, err = cleanup_sync_remove_stale_items()
        if err:
            flash(f"Cleanup failed: {err}", "danger")
        else:
            flash(f"Cleanup complete: {dip_deleted} items removed from DIP, {ras_deleted} items removed from RASALKHORE.", "success")
    finally:
        with _sync_lock:
            _cleanup_in_progress = False
    return redirect(url_for("upload_file"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username in USERS and check_password_hash(USERS[username], password):
            session["username"] = username
            flash("Login successful!", "success")
            return redirect(url_for("home"))
        else:
            flash("Invalid credentials", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("Logged out successfully!", "info")
    return redirect(url_for("home"))


@app.route("/uploadstock", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        # Check if this is an API sync request
        if "sync_from_api" in request.form:
            keep_admin_prices = request.form.get("keep_admin_prices") == "on"
            results = sync_all_warehouses_from_api(keep_admin_prices=keep_admin_prices)
            success_count = sum(1 for r in results.values() if r["success"])
            total_items = sum(r["items_updated"] for r in results.values())
            return render_template("upload.html", api_sync_results=results, api_sync_success=True, 
                                 success_count=success_count, total_items=total_items)
        
        # Otherwise, handle Excel upload (existing functionality)
        keep_admin_prices = request.form.get("keep_admin_prices") == "on"
        if "file" not in request.files:
            return "No file part", 400
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400
        if file and allowed_file(file.filename):
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], "stock_details.xlsx")
            file.save(filepath)
            process_excel(filepath, keep_admin_prices=keep_admin_prices)  # pass the flag
            return render_template("home.html")
    return render_template("upload.html")

def process_excel(filepath, keep_admin_prices=True):
    """Read sheets OUTPUT_DIP, OUTPUT_RAS and ALABAMA from Excel and update the databases."""
    xls = pd.ExcelFile(filepath)

    BRANCH_SHEETS = {
        "DIP": "OUTPUT_DIP",
        "RASALKHORE": "OUTPUT_RAS",
        "ALABAMA": "ALABAMA",
    }

    for branch, sheet_name in BRANCH_SHEETS.items():
        if sheet_name not in xls.sheet_names:
            continue

        # Read with Item No. as string so ItemCode stays consistent
        df = pd.read_excel(xls, sheet_name=sheet_name, dtype={'Item No.': str})

        # --- Branch-specific normalization ---
        if branch == "ALABAMA":
            # Alabama has no stock / min selling price in your app
            column_mapping = {
                "Item No.": "ItemCode",
                "Item Description": "Description",
                "Manufacturer Name": "Manufacturer Name",
                "Upc Code": "Upc Code",
                "Cost Price": "CostPrice",
            }
            df.rename(columns=column_mapping, inplace=True)

            # Keep only columns we need
            keep_cols = ["ItemCode", "Upc Code", "Description", "Manufacturer Name", "CostPrice"]
            for col in keep_cols:
                if col not in df.columns:
                    # sensible defaults
                    df[col] = "" if col != "CostPrice" else ""
            df = df[keep_cols]

            # Cleanup
            df["ItemCode"] = df["ItemCode"].fillna("").astype(str).str.strip()
            df["Upc Code"] = df["Upc Code"].fillna("").astype(str).str.strip()
            df["Description"] = df["Description"].fillna("").astype(str).str.strip()
            df["Manufacturer Name"] = df["Manufacturer Name"].fillna("").astype(str).str.strip()
            df["CostPrice"] = df["CostPrice"].fillna("").astype(str).str.strip()

        else:
            # DIP & RASALKHORE share the same base headings
            column_mapping = {
                "Item No.": "ItemCode",
                "Item Description": "Description",
                "Upc Code": "Upc Code",
                "Manufacturer Name": "Manufacturer Name",
                "Warehouse Code": "Warehouse Code",
                "In Stock": "Stock Quantity",
                "FREE STOCK": "Free Stock",
                "Minimum Selling Price": "Selling Price",
                "Cost Price": "CostPrice",
            }
            df.rename(columns=column_mapping, inplace=True)

            # Debug (optional)
            print(f"Before filling NaN - Data from Excel for {branch}:")
            cols_for_print = [c for c in ["ItemCode", "Upc Code", "Description"] if c in df.columns]
            if cols_for_print:
                print(df[cols_for_print].head(10))

            # Cleanup common fields
            df["ItemCode"] = df["ItemCode"].astype(str).str.strip()
            if "Upc Code" in df.columns:
                df["Upc Code"] = df["Upc Code"].fillna("").astype(str).str.strip()
            if "Selling Price" in df.columns:
                df["Selling Price"] = df["Selling Price"].fillna("").astype(str).str.strip()
            if "CostPrice" in df.columns:
                df["CostPrice"] = df["CostPrice"].fillna("").astype(str).str.strip()

            df.columns = df.columns.str.strip()
            df.fillna(0, inplace=True)

            # Expected base columns for non-ALABAMA pages
            expected = [
                "ItemCode", "Upc Code", "Description", "Manufacturer Name",
                "Warehouse Code", "Stock Quantity", "Free Stock", "Selling Price", "CostPrice",
            ]
            for col in expected:
                if col not in df.columns:
                    df[col] = 0 if col in ["Stock Quantity", "Free Stock"] else ""

            if branch == "DIP":
                # ✅ Keep the six retail branch stock columns from OUTPUT_DIP
                for b in RETAIL_BRANCHES:
                    if b not in df.columns:
                        df[b] = 0
                    # ensure numeric (blank/invalid → 0)
                    df[b] = pd.to_numeric(df[b], errors="coerce").fillna(0)

                # Order: base expected + retail stocks
                df = df[expected + RETAIL_BRANCHES]
            else:
                # RASALKHORE stays as base expected columns
                df = df[expected]

        # --- Persist into the per-branch DB ---
        update_database(branch, df, keep_admin_prices=keep_admin_prices)

        # (Optional but harmless) ensure the retail override table exists in DIP DB
        if branch == "DIP":
            try:
                ensure_retail_override_table(DB_PATHS["DIP"])
            except Exception:
                # don’t hard-fail import if helper isn’t present
                pass



def ensure_override_table(db_path: str):
    """Ensure price_overrides table exists with retry logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, timeout=5.0) as conn:
                cur = conn.cursor()
                # Base table (if new DB)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS price_overrides (
                        ItemCode TEXT PRIMARY KEY,
                        SellingPriceOverride REAL,   -- used by DIP/RASALKHORE
                        CostPriceOverride    REAL,   -- used by ALABAMA
                        edited_by TEXT,
                        edited_at TEXT DEFAULT (datetime('now'))
                    )
                """)
                # If the table already existed without one of the columns, add it.
                cur.execute("PRAGMA table_info(price_overrides)")
                cols = {r[1] for r in cur.fetchall()}
                if "SellingPriceOverride" not in cols:
                    cur.execute('ALTER TABLE price_overrides ADD COLUMN SellingPriceOverride REAL')
                if "CostPriceOverride" not in cols:
                    cur.execute('ALTER TABLE price_overrides ADD COLUMN CostPriceOverride REAL')
                if "edited_by" not in cols:
                    cur.execute('ALTER TABLE price_overrides ADD COLUMN edited_by TEXT')
                if "edited_at" not in cols:
                    cur.execute("ALTER TABLE price_overrides ADD COLUMN edited_at TEXT DEFAULT (datetime('now'))")
            break  # Success, exit retry loop
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            else:
                raise

def ensure_stock_items_indexes(db_path: str):
    """
    Create indexes on stock_items table for faster searches.
    This significantly improves search query performance.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with get_db_connection(db_path, timeout=5.0) as conn:
                cur = conn.cursor()
                
                # Check if stock_items table exists
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
                if not cur.fetchone():
                    return
                
                # Create indexes if they don't exist
                indexes = [
                    ('idx_stock_items_itemcode', 'stock_items', '"ItemCode"'),
                    ('idx_stock_items_upc', 'stock_items', '"Upc Code"'),
                    ('idx_stock_items_description', 'stock_items', '"Description"'),
                    ('idx_stock_items_manufacturer', 'stock_items', '"Manufacturer Name"'),
                ]
                
                for idx_name, table_name, column in indexes:
                    try:
                        # Check if index already exists
                        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (idx_name,))
                        if not cur.fetchone():
                            # Create index with IF NOT EXISTS equivalent (using try/except)
                            cur.execute(f'CREATE INDEX {idx_name} ON {table_name}({column})')
                            print(f"Created index: {idx_name}")
                    except sqlite3.Error as e:
                        # Index might already exist or other error
                        print(f"Index creation warning for {idx_name}: {e}")
            break
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (2 ** attempt))
                continue
            else:
                raise

def update_database(branch, df, keep_admin_prices=True):
    """Persist DataFrame into per-branch DB using INSERT OR REPLACE (safe, no table drop)."""
    db_path = DB_PATHS[branch]

    with get_db_connection(db_path, timeout=30.0) as conn:
        cur = conn.cursor()

        # Create stock_items if missing
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
        if not cur.fetchone():
            if branch == "DIP":
                cur.execute("""
                    CREATE TABLE stock_items (
                        "ItemCode" TEXT PRIMARY KEY, "Upc Code" TEXT, "Description" TEXT,
                        "Manufacturer Name" TEXT, "Warehouse Code" TEXT,
                        "Stock Quantity" REAL DEFAULT 0, "Free Stock" REAL DEFAULT 0,
                        "Selling Price" REAL DEFAULT 0, "CostPrice" REAL DEFAULT 0,
                        "AJMAN" REAL DEFAULT 0, "NAH" REAL DEFAULT 0, "DEIRA" REAL DEFAULT 0,
                        "DEIRA2" REAL DEFAULT 0, "ABUDHABI" REAL DEFAULT 0, "QUSAIS" REAL DEFAULT 0
                    )
                """)
            else:
                cur.execute("""
                    CREATE TABLE stock_items (
                        "ItemCode" TEXT PRIMARY KEY, "Upc Code" TEXT, "Description" TEXT,
                        "Manufacturer Name" TEXT, "Warehouse Code" TEXT,
                        "Stock Quantity" REAL DEFAULT 0, "Free Stock" REAL DEFAULT 0,
                        "Selling Price" REAL DEFAULT 0, "CostPrice" REAL DEFAULT 0
                    )
                """)

        columns = list(df.columns)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT OR REPLACE INTO stock_items ({col_names}) VALUES ({placeholders})'

        for _, row in df.iterrows():
            cur.execute(sql, tuple(row[c] for c in columns))

    ensure_override_table(db_path)

    if not keep_admin_prices:
        with get_db_connection(db_path, timeout=10.0) as conn:
            conn.execute("DELETE FROM price_overrides")

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/a")
def alabama_home():
    return render_template("alabama_home.html")

# # For debugging: list the tables in the database
# conn = sqlite3.connect(DB_PATH)
# cursor = conn.cursor()
# cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
# print("Tables in DB:", cursor.fetchall())
# conn.close()
@app.route("/headoffice", methods=["GET", "POST"])
def headoffice():
    return stock_page("DIP")

@app.route("/rasalkhor", methods=["GET", "POST"])
def rasalkhor():
    return stock_page("RASALKHORE")

@app.route("/alabama", methods=["GET", "POST"])
def alabama():
    return stock_page("ALABAMA")

def stock_page(branch):
    results = None
    query = ""
    hide_zero_stock = False
    hide_zero_cost = False

    if request.method == "POST":
        query = request.form.get("query", "").strip().lower()
        if branch != "ALABAMA":
            hide_zero_stock = request.form.get("hideZeroStock") == "on"
        else:
            hide_zero_cost = request.form.get("hideZeroCost") == "on"

        if query:
            db_path = DB_PATHS[branch]
            
            # make sure overrides table exists for JOINs
            ensure_override_table(db_path)
            ensure_brand_margins_table(DB_PATHS["DIP"])
            if branch == "RASALKHORE":
                ensure_override_table(DB_PATHS["DIP"])  # DIP price_overrides for DIP+5% calc
            
            # Use connection helper with retry logic
            try:
                with get_db_connection(db_path, timeout=10.0) as conn:
                    cursor = conn.cursor()

                    # words for filtering
                    query_words = query.split()

                    # --- Build SELECT per-branch ---
                    if branch == "ALABAMA":
                        # ALABAMA shows all items from Junaid (DIP + RASALKHORE) with calculated Cost and Selling Price
                        # Attach both DIP and RASALKHORE databases
                        dip_db_path = os.path.abspath(DB_PATHS["DIP"])
                        ras_db_path = os.path.abspath(DB_PATHS["RASALKHORE"])
                        alabama_db_path = os.path.abspath(DB_PATHS["ALABAMA"])
                        cursor.execute(f'ATTACH DATABASE "{dip_db_path}" AS dip')
                        cursor.execute(f'ATTACH DATABASE "{ras_db_path}" AS ras')
                        cursor.execute(f'ATTACH DATABASE "{alabama_db_path}" AS alabama')
                        
                        # Get Junaid cost price (prefer DIP, fallback to RASALKHORE)
                        # We'll calculate Alabama Cost and Selling Price in Python after fetching
                        # IMPORTANT: wrap UNION in a subquery so we can safely append search filters (SQLite limitation)
                        sql_query = """
                    SELECT
                        t.ItemCode            AS "ItemCode",
                        t.UpcCode             AS "Upc Code",
                        t.Description         AS "Description",
                        t.ManufacturerName    AS "Manufacturer Name",
                        t.JunaidCost          AS "JunaidCost",
                        t.CostOverride        AS "CostOverride",
                        t.SellingOverride     AS "SellingOverride"
                    FROM (
                        SELECT
                            dip_si."ItemCode"             AS ItemCode,
                            dip_si."Upc Code"             AS UpcCode,
                            dip_si."Description"          AS Description,
                            dip_si."Manufacturer Name"    AS ManufacturerName,
                            dip_si."CostPrice"            AS JunaidCost,
                            po.CostPriceOverride          AS CostOverride,
                            po.SellingPriceOverride      AS SellingOverride
                        FROM dip.stock_items dip_si
                        LEFT JOIN alabama.price_overrides po ON po.ItemCode = dip_si."ItemCode"
                        WHERE dip_si."ItemCode" IS NOT NULL
                        
                        UNION
                        
                        SELECT
                            ras_si."ItemCode"              AS ItemCode,
                            ras_si."Upc Code"              AS UpcCode,
                            ras_si."Description"           AS Description,
                            ras_si."Manufacturer Name"     AS ManufacturerName,
                            ras_si."CostPrice"             AS JunaidCost,
                            po.CostPriceOverride           AS CostOverride,
                            po.SellingPriceOverride        AS SellingOverride
                        FROM ras.stock_items ras_si
                        LEFT JOIN dip.stock_items dip_si ON dip_si."ItemCode" = ras_si."ItemCode"
                        LEFT JOIN alabama.price_overrides po ON po.ItemCode = ras_si."ItemCode"
                        WHERE dip_si."ItemCode" IS NULL AND ras_si."ItemCode" IS NOT NULL
                    ) t
                    WHERE
                """
                        col_item = 't.ItemCode'
                        col_upc  = 't.UpcCode'
                        col_desc = 't.Description'
                        col_mfg  = 't.ManufacturerName'
                    else:
                        # Non-ALABAMA
                        if branch == "DIP":
                            # Attach RAS DB to show RAS stock alongside DIP
                            ras_db_path = os.path.abspath(DB_PATHS["RASALKHORE"])
                            cursor.execute(f'ATTACH DATABASE "{ras_db_path}" AS ras')

                            sql_query = """
                        SELECT
                            si."ItemCode",               -- 0
                            si."Upc Code",               -- 1
                            si."Description",            -- 2
                            si."Manufacturer Name",      -- 3
                            si."Warehouse Code",         -- 4
                            si."Stock Quantity"      AS "DIP Stock",            -- 5
                            COALESCE(rsi."Stock Quantity", 0) AS "RAS Stock",  -- 6
                            si."Free Stock",                                    -- 7
                            CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END AS "Selling Price", -- 8
                            si."CostPrice" ,                                     -- 9
                            (COALESCE(si."Stock Quantity",0) + COALESCE(rsi."Stock Quantity",0)) AS "Total Stock" -- 10
                        FROM stock_items si
                        LEFT JOIN ras.stock_items rsi ON rsi."ItemCode" = si."ItemCode"
                        LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
                        LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
                        WHERE
                    """
                            col_item = 'si."ItemCode"'
                            col_upc  = 'si."Upc Code"'
                            col_desc = 'si."Description"'
                            col_mfg  = 'si."Manufacturer Name"'
                        else:
                            # RASALKHORE page: DIP price + 5% (no price overrides)
                            ras_db_path = os.path.abspath(DB_PATHS["RASALKHORE"])
                            dip_db_path = os.path.abspath(DB_PATHS["DIP"])
                            cursor.execute(f'ATTACH DATABASE "{dip_db_path}" AS dip')
                            sql_query = """
                        SELECT
                            si."ItemCode",
                            si."Upc Code",
                            si."Description",
                            si."Manufacturer Name",
                            si."Warehouse Code",
                            si."Stock Quantity",
                            si."Free Stock",
                            ROUND((CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(dip_si."CostPrice", si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(COALESCE(dip_si."CostPrice", si."CostPrice") AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN COALESCE(dip_si."Selling Price", si."Selling Price")
     ELSE COALESCE(dip_po.SellingPriceOverride, dip_si."Selling Price", si."Selling Price") END) * 1.05, 2) AS "Selling Price",
                            si."CostPrice"
                                FROM stock_items si
                                LEFT JOIN dip.stock_items dip_si ON dip_si."ItemCode" = si."ItemCode"
                                LEFT JOIN dip.price_overrides dip_po ON dip_po.ItemCode = dip_si."ItemCode"
                                LEFT JOIN dip.brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(COALESCE(dip_si."Manufacturer Name", si."Manufacturer Name")))
                                WHERE
                            """
                            col_item = 'si."ItemCode"'
                            col_upc  = 'si."Upc Code"'
                            col_desc = 'si."Description"'
                            col_mfg  = 'si."Manufacturer Name"'

                    # --- WHERE conditions (shared) ---
                    conditions = []
                    params = []
                    for w in query_words:
                        like = f"%{w}%"
                        conditions.append(
                            f"""(
                                LOWER({col_item}) LIKE ? OR
                                LOWER({col_upc})  LIKE ? OR
                                LOWER({col_desc}) LIKE ? OR
                                LOWER({col_mfg})  LIKE ?
                            )"""
                        )
                        params.extend([like, like, like, like])

                    sql_query += " AND ".join(conditions)

                    # Extra filters
                    if branch != "ALABAMA" and hide_zero_stock:
                        # Changed 0 to 10, and added CAST to fix number comparison
                        sql_query += ' AND CAST(si."Stock Quantity" AS REAL) > 0'
                    if branch == "ALABAMA" and hide_zero_cost:
                        sql_query += ' AND CAST("JunaidCost" AS REAL) > 0'

                    # --- Execute ---
                    cursor.execute(sql_query, params)
                    results = cursor.fetchall()

                    # If ALABAMA page, calculate Cost and Selling Price from Junaid data
                    if branch == "ALABAMA":
                        # Load Alabama margins
                        alabama_db = DB_PATHS["ALABAMA"]
                        ensure_alabama_margins_table(alabama_db)
                        try:
                            with get_db_connection(alabama_db, timeout=10.0) as alabama_conn:
                                alabama_cur = alabama_conn.cursor()
                                alabama_cur.execute("SELECT brand_name, cost_margin_percent, brand_margin_percent FROM alabama_margins")
                                alabama_margins_map = {}
                                default_cost_margin = 10.0
                                default_brand_margin = 15.0
                                for row in alabama_cur.fetchall():
                                    if row[0] == "__DEFAULT__":
                                        default_cost_margin = row[1]
                                        default_brand_margin = row[2]
                                    else:
                                        alabama_margins_map[row[0].lower()] = (row[1], row[2])
                        except sqlite3.OperationalError:
                            # If locked, use defaults
                            alabama_margins_map = {}
                            default_cost_margin = 10.0
                            default_brand_margin = 15.0
                        
                        # Process results: Calculate Alabama Cost and Selling Price
                        # Results format: (ItemCode, Upc Code, Description, Manufacturer Name, JunaidCost, CostOverride, SellingOverride)
                        processed_results = []
                        for row in results:
                            item_code, upc_code, description, manufacturer, junaid_cost, cost_override, selling_override = row
                            
                            # Use override if exists, otherwise use Junaid cost
                            base_cost = float(cost_override) if cost_override is not None else float(junaid_cost or 0)
                            
                            # Get margins for this manufacturer (case-insensitive)
                            manufacturer_lower = (manufacturer or "").lower()
                            if manufacturer_lower in alabama_margins_map:
                                cost_margin, brand_margin = alabama_margins_map[manufacturer_lower]
                            else:
                                cost_margin = default_cost_margin
                                brand_margin = default_brand_margin
                            
                            # Calculate Alabama Cost = Junaid Cost * (1 + cost_margin/100)
                            alabama_cost = round(base_cost * (1 + cost_margin / 100), 2) if base_cost > 0 else 0.0
                            
                            # Selling price: use override if exists, else calculate from cost + brand margin
                            if selling_override is not None and float(selling_override) >= 0:
                                alabama_selling_price = round(float(selling_override), 2)
                            else:
                                margin_divisor = 1 - (brand_margin / 100)
                                alabama_selling_price = round(alabama_cost / margin_divisor, 2) if alabama_cost > 0 and margin_divisor > 0 else 0.0
                            
                            # Return: ItemCode, Upc Code, Description, Manufacturer Name, CostPrice, Selling Price
                            processed_results.append((
                                item_code,
                                upc_code or "",
                                description or "",
                                manufacturer or "",
                                alabama_cost,
                                alabama_selling_price
                            ))
                        
                        results = processed_results
                        
                        # Detach databases
                        try:
                            cursor.execute("DETACH DATABASE dip")
                            cursor.execute("DETACH DATABASE ras")
                            cursor.execute("DETACH DATABASE alabama")
                        except:
                            pass

                    # If DIP page, append Sold Stock as last column (index 10)
                    elif branch == "DIP":
                        if session.get("username"):
                            # OPTIMIZATION: Only fetch sold data if we have results (don't block on empty search)
                            sold_map = {}
                            if results and len(results) > 0:
                                try:
                                    sold_map = fetch_sold_breakdown_map()
                                except Exception as e:
                                    print(f"Sold breakdown API error (non-blocking): {e}")
                                    sold_map = {}

                            def _g(code, key):
                                return (sold_map.get(code, {}) or {}).get(key, 0.0)

                            results = [
                                row + (
                                    _g(str(row[0]).strip(), "total"),
                                    _g(str(row[0]).strip(), "ho"),
                                    _g(str(row[0]).strip(), "others"),
                                    _g(str(row[0]).strip(), "total_2025"),
                                    _g(str(row[0]).strip(), "total_2026"),
                                )
                                for row in results
                            ]

                    # Detach attached DB (only if we attached it)
                    if branch == "DIP":
                        try:
                            cursor.execute("DETACH DATABASE ras")
                        except Exception:
                            pass
            except sqlite3.OperationalError as e:
                # If database is locked, return empty results with error message
                print(f"Database locked in stock_page: {e}")
                results = []
    # total_value = None
    # if "username" in session and branch != "ALABAMA":
    #     db_path = DB_PATHS[branch]
    #     conn = sqlite3.connect(db_path)
    #     cur = conn.cursor()
    #     cur.execute("""
    #         SELECT
    #             SUM(
    #                 CAST("Stock Quantity" AS REAL) * CAST("CostPrice" AS REAL)
    #             )
    #         FROM stock_items
    #         WHERE CAST("Stock Quantity" AS REAL) > 0
    #         AND CAST("CostPrice" AS REAL) > 0
    #     """)
    #     val = cur.fetchone()[0]
    #     conn.close()
    #     total_value = round(val or 0, 2)

    # ---- compute filtered totals for current results (only when logged in) ----
    dip_total_value = None
    ras_total_value = None
    matched_count = 0
    branch_totals = None  # NEW: will hold DIP/RAS + all retail totals

    if "username" in session and results:
        matched_count = len(results)
        try:
            if branch == "DIP":
                # From results (indexes per your SELECT):
                # 5 = DIP Stock, 6 = RAS Stock, 9 = Cost
                dip_total_value = round(sum(float(r[5] or 0) * float(r[9] or 0) for r in results), 2)
                ras_total_value = round(sum(float(r[6] or 0) * float(r[9] or 0) for r in results), 2)

                # Build a list of matched item codes
                item_codes = [str(r[0]).strip() for r in results if r and r[0]]
                branch_totals = {
                    "DIP": dip_total_value,
                    "RAS": ras_total_value,
                    "AJMAN": 0.0, "NAH": 0.0, "DEIRA": 0.0, "DEIRA2": 0.0, "ABUDHABI": 0.0, "QUSAIS": 0.0,
                }

                if item_codes:
                    # OPTIMIZATION: Limit query to reasonable number of items (prevent huge IN clause)
                    # If too many results, calculate totals from results directly instead
                    if len(item_codes) > 1000:
                        # For large result sets, calculate from already-fetched data
                        # This avoids slow IN queries with thousands of parameters
                        for r in results:
                            cost = float(r[9] or 0)  # Cost at index 9
                            # Note: Retail stocks not in main query, so skip branch totals for large sets
                        pass  # Skip branch totals calculation for performance
                    else:
                        placeholders = ",".join(["?"] * len(item_codes))
                        dip_db_path = DB_PATHS["DIP"]
                        try:
                            with get_db_connection(dip_db_path, timeout=15.0) as conn2:
                                cur2 = conn2.cursor()
                                cur2.execute(f'''
                                    SELECT
                                        CAST(COALESCE(SUM(CAST(si."AJMAN" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS aj_total,
                                        CAST(COALESCE(SUM(CAST(si."NAH" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS nah_total,
                                        CAST(COALESCE(SUM(CAST(si."DEIRA" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS deira_total,
                                        CAST(COALESCE(SUM(CAST(si."DEIRA2" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS deira2_total,
                                        CAST(COALESCE(SUM(CAST(si."ABUDHABI" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS abu_total,
                                        CAST(COALESCE(SUM(CAST(si."QUSAIS" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS qus_total
                                    FROM stock_items si
                                    WHERE si."ItemCode" IN ({placeholders})
                                ''', item_codes)
                                row = cur2.fetchone()
                                if row:
                                    branch_totals["AJMAN"] = round(float(row[0] or 0), 2)
                                    branch_totals["NAH"] = round(float(row[1] or 0), 2)
                                    branch_totals["DEIRA"] = round(float(row[2] or 0), 2)
                                    branch_totals["DEIRA2"] = round(float(row[3] or 0), 2)
                                    branch_totals["ABUDHABI"] = round(float(row[4] or 0), 2)
                                    branch_totals["QUSAIS"] = round(float(row[5] or 0), 2)
                        except sqlite3.OperationalError:
                            pass  # Skip branch totals if DB locked

                    # Totals already rounded above (removed redundant loop)

            elif branch == "RASALKHORE":
                # 5 = RAS Stock, 8 = Cost
                ras_total_value = round(sum(float(r[5] or 0) * float(r[8] or 0) for r in results), 2)

                # For DIP value, only for matched items
                item_codes = [str(r[0]).strip() for r in results if r and r[0]]
                dip_total_value = 0.0
                if item_codes:
                    # OPTIMIZATION: Use SQL aggregation instead of Python loop
                    if len(item_codes) > 1000:
                        # For large result sets, calculate from already-fetched data
                        dip_total_value = round(sum(float(r[5] or 0) * float(r[8] or 0) for r in results), 2)
                    else:
                        placeholders = ",".join(["?"] * len(item_codes))
                        dip_db_path = DB_PATHS["DIP"]
                        try:
                            with get_db_connection(dip_db_path, timeout=15.0) as conn2:
                                cur2 = conn2.cursor()
                                cur2.execute(f'''
                                    SELECT CAST(COALESCE(SUM(CAST(si."Stock Quantity" AS REAL) * CAST(si."CostPrice" AS REAL)), 0) AS REAL) AS total
                                    FROM stock_items si
                                    WHERE si."ItemCode" IN ({placeholders})
                                ''', item_codes)
                                row = cur2.fetchone()
                                dip_total_value = round(float(row[0] or 0), 2) if row else 0.0
                        except sqlite3.OperationalError:
                            pass  # Skip if DB locked

        except Exception:
            dip_total_value = 0.0 if dip_total_value is None else dip_total_value
            ras_total_value = 0.0 if ras_total_value is None else ras_total_value

    ctx = dict(
        results=results,
        query=query,
        hide_zero_stock=hide_zero_stock,
        hide_zero_cost=hide_zero_cost,
        branch=branch,
        dip_total_value=dip_total_value,
        ras_total_value=ras_total_value,
        matched_count=matched_count,
        branch_totals=branch_totals,
    )

    # If called via AJAX, return only the results/totals block (no full page reload)
    if request.args.get("partial") == "1" or request.headers.get("X-Partial") == "1":
        return render_template("_stock_results.html", **ctx)

    return render_template("stock.html", **ctx)
@app.route("/item/<branch>/<item_code>")
def item_detail(branch, item_code):
    branch = (branch or "").upper()
    item_code = (item_code or "").strip()

    # ==========================================
    # 1. SPECIFIC LOGIC FOR "ALLSTORES" (Must be first!)
    # ==========================================
    if branch == "ALLSTORES":
        dip_db = DB_PATHS["DIP"]
        ras_db_path = os.path.abspath(DB_PATHS["RASALKHORE"])
        
        # Ensure tables exist
        try: ensure_retail_override_table(dip_db)
        except: pass
        try: ensure_override_table(dip_db) # For generic admin overrides
        except: pass
        try: ensure_brand_margins_table(dip_db)
        except: pass

        conn = sqlite3.connect(dip_db)
        cur = conn.cursor()
        
        # Attach RAS to get that stock
        cur.execute(f"ATTACH DATABASE '{ras_db_path}' AS ras")

        # Query with robust Joins and Fallback Pricing
        cur.execute("""
            SELECT
                si."ItemCode",              -- 0
                si."Upc Code",              -- 1
                si."Description",           -- 2
                si."Manufacturer Name",     -- 3
                si."Warehouse Code",        -- 4
                
                -- Individual Branch Stocks
                COALESCE(si."AJMAN", 0),    -- 5
                COALESCE(si."NAH", 0),      -- 6
                COALESCE(si."DEIRA", 0),    -- 7
                COALESCE(si."DEIRA2", 0),   -- 8
                COALESCE(si."ABUDHABI", 0), -- 9
                COALESCE(si."QUSAIS", 0),   -- 10
                COALESCE(rsi."Stock Quantity", 0) AS RAS_Stock, -- 11
                
                -- Calculated Total
                (
                    COALESCE(si."AJMAN", 0) + COALESCE(si."NAH", 0) +
                    COALESCE(si."DEIRA", 0) + COALESCE(si."DEIRA2", 0) +
                    COALESCE(si."ABUDHABI", 0) + COALESCE(si."QUSAIS", 0) +
                    COALESCE(rsi."Stock Quantity", 0)
                ) AS TotalStock,            -- 12
                
                -- PRICE LOGIC: DIP price + 5% (no retail overrides)
                ROUND((CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
                     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
                     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
                     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END) * 1.05, 2) AS MinPrice, -- 13
                si."CostPrice"              -- 14
            FROM stock_items si
            LEFT JOIN ras.stock_items rsi ON TRIM(rsi."ItemCode") = TRIM(si."ItemCode")
            LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
            LEFT JOIN price_overrides po
                ON TRIM(po.ItemCode) = TRIM(si."ItemCode")
            WHERE TRIM(si."ItemCode") = TRIM(?)
        """, (item_code,))
        
        row = cur.fetchone()
        
        # Detach and clean up
        try: cur.execute("DETACH DATABASE ras")
        except: pass
        conn.close()

        if not row:
            return render_template("item_detail.html", item=None, branch=branch), 404

        item_data = {
            "ItemCode": row[0],
            "UpcCode": row[1],
            "Description": row[2],
            "ManufacturerName": row[3],
            "WarehouseCode": row[4],
            # Breakdown
            "AJMAN": row[5],
            "NAH": row[6],
            "DEIRA": row[7],
            "DEIRA2": row[8],
            "ABUDHABI": row[9],
            "QUSAIS": row[10],
            "RAS": row[11],
            # Totals & Price
            "TotalStock": row[12],
            "MinSellingPrice": row[13], 
            "CostPrice": row[14] if "username" in session else None,
        }
        return render_template("item_detail.html", item=item_data, branch=branch)

    # ==========================================
    # 2. GENERIC RETAIL BRANCHES (AJMAN, NAH, etc.)
    # ==========================================
    if branch in RETAIL_BRANCHES:
        db_path = DB_PATHS["DIP"]
        try: ensure_retail_override_table(db_path)
        except: pass

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        ensure_brand_margins_table(db_path)
        cur.execute(f"""
            SELECT
                si."ItemCode",
                si."Upc Code",
                si."Description",
                si."Manufacturer Name",
                si."Warehouse Code",
                COALESCE(si."{branch}", 0) AS retail_stock,
                0 AS free_stock,
                CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(ro.SellingPriceOverride, si."Selling Price") END AS eff_min_price,
                si."CostPrice"
            FROM stock_items si
            LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
            LEFT JOIN retail_overrides ro
              ON TRIM(ro.ItemCode) = TRIM(si."ItemCode") AND ro.Branch = ?
            WHERE TRIM(si."ItemCode") = TRIM(?)
        """, (branch, item_code))
        row = cur.fetchone()
        conn.close()

        if not row: return render_template("item_detail.html", item=None, branch=branch), 404

        item_data = {
            "ItemCode": row[0], "UpcCode": row[1], "Description": row[2],
            "ManufacturerName": row[3], "WarehouseCode": row[4],
            "StockQuantity": row[5], "FreeStock": row[6],
            "MinSellingPrice": row[7], 
            "CostPrice": row[8] if "username" in session else None,
        }
        return render_template("item_detail.html", item=item_data, branch=branch)

    # ==========================================
    # 3. ALABAMA
    # ==========================================
    if branch == "ALABAMA":
        # Get item from DIP or RASALKHORE (prefer DIP)
        dip_db = DB_PATHS["DIP"]
        ras_db = DB_PATHS["RASALKHORE"]
        alabama_db = DB_PATHS["ALABAMA"]
        
        # Try DIP first
        dip_conn = sqlite3.connect(dip_db)
        dip_cur = dip_conn.cursor()
        dip_cur.execute('SELECT "ItemCode", "Upc Code", "Description", "Manufacturer Name", "CostPrice" FROM stock_items WHERE "ItemCode" = ?', (item_code,))
        item = dip_cur.fetchone()
        dip_conn.close()
        
        # If not in DIP, try RASALKHORE
        if not item:
            ras_conn = sqlite3.connect(ras_db)
            ras_cur = ras_conn.cursor()
            ras_cur.execute('SELECT "ItemCode", "Upc Code", "Description", "Manufacturer Name", "CostPrice" FROM stock_items WHERE "ItemCode" = ?', (item_code,))
            item = ras_cur.fetchone()
            ras_conn.close()

        if item:
            # Get cost and selling overrides if exist
            alabama_conn = sqlite3.connect(alabama_db)
            alabama_cur = alabama_conn.cursor()
            ensure_override_table(alabama_db)
            alabama_cur.execute('SELECT CostPriceOverride, SellingPriceOverride FROM price_overrides WHERE ItemCode = ?', (item_code,))
            override_row = alabama_cur.fetchone()
            cost_override = override_row[0] if override_row else None
            selling_override = override_row[1] if override_row and len(override_row) > 1 else None
            
            # Get Alabama margins
            ensure_alabama_margins_table(alabama_db)
            manufacturer = item[3] or ""
            cost_margin, brand_margin = get_alabama_margins(alabama_db, manufacturer)
            alabama_conn.close()
            
            # Calculate prices
            junaid_cost = float(cost_override) if cost_override is not None else float(item[4] or 0)
            alabama_cost = round(junaid_cost * (1 + cost_margin / 100), 2) if junaid_cost > 0 else 0.0
            if selling_override is not None and float(selling_override) >= 0:
                alabama_selling_price = round(float(selling_override), 2)
            else:
                margin_divisor = 1 - (brand_margin / 100)
                alabama_selling_price = round(alabama_cost / margin_divisor, 2) if alabama_cost > 0 and margin_divisor > 0 else 0.0
            
            item_data = {
                "ItemCode": item[0], "UpcCode": item[1], "Description": item[2],
                "ManufacturerName": item[3], "WarehouseCode": None,
                "StockQuantity": None, "FreeStock": None,
                "CostPrice": alabama_cost if "username" in session else None,
                "MinSellingPrice": alabama_selling_price if "username" in session else None,
            }
            return render_template("item_detail.html", item=item_data, branch=branch)
        return render_template("item_detail.html", item=None, branch=branch), 404

    # ==========================================
    # 4. HEAOFFICE / RASALKHORE (Standard)
    # ==========================================
    db_path = DB_PATHS.get(branch)
    if not db_path:
        return render_template("item_detail.html", item=None, branch=branch), 404

    ensure_override_table(db_path)
    ensure_brand_margins_table(db_path)
    if branch == "RASALKHORE":
        ensure_override_table(DB_PATHS["DIP"])
        ensure_brand_margins_table(DB_PATHS["DIP"])
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if branch == "RASALKHORE":
        dip_path = os.path.abspath(DB_PATHS["DIP"])
        cur.execute(f'ATTACH DATABASE "{dip_path}" AS dip')
        cur.execute("""
            SELECT
                si."ItemCode", si."Upc Code", si."Description", si."Manufacturer Name", si."Warehouse Code",
                si."Stock Quantity", si."Free Stock",
                ROUND((CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(dip_si."CostPrice", si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(COALESCE(dip_si."CostPrice", si."CostPrice") AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN COALESCE(dip_si."Selling Price", si."Selling Price")
     ELSE COALESCE(dip_po.SellingPriceOverride, dip_si."Selling Price", si."Selling Price") END) * 1.05, 2) AS "Selling Price",
                si."CostPrice"
            FROM stock_items si
            LEFT JOIN dip.stock_items dip_si ON dip_si."ItemCode" = si."ItemCode"
            LEFT JOIN dip.price_overrides dip_po ON dip_po.ItemCode = dip_si."ItemCode"
            LEFT JOIN dip.brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(COALESCE(dip_si."Manufacturer Name", si."Manufacturer Name")))
            WHERE TRIM(si."ItemCode") = TRIM(?)
        """, (item_code,))
    else:
        cur.execute("""
            SELECT
                si."ItemCode", si."Upc Code", si."Description", si."Manufacturer Name", si."Warehouse Code",
                si."Stock Quantity", si."Free Stock",
CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END AS "Selling Price",
            si."CostPrice"
        FROM stock_items si
        LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
        LEFT JOIN price_overrides po ON TRIM(po.ItemCode) = TRIM(si."ItemCode")
        WHERE TRIM(si."ItemCode") = TRIM(?)
        """, (item_code,))
    item = cur.fetchone()
    conn.close()

    if item:
        item_data = {
            "ItemCode": item[0], "UpcCode": item[1], "Description": item[2],
            "ManufacturerName": item[3], "WarehouseCode": item[4],
            "StockQuantity": item[5], "FreeStock": item[6],
            "MinSellingPrice": item[7], 
            "CostPrice": item[8] if "username" in session else None,
        }
        return render_template("item_detail.html", item=item_data, branch=branch)

    return render_template("item_detail.html", item=None, branch=branch), 404

# (Optional) Route to update data manually - DISABLED (use API sync instead)
# @app.route("/update_data/<branch>", methods=["GET"])
# def update_data(branch):
#     if branch not in DB_PATHS:
#         return f"Branch '{branch}' not found.", 404
#     # Use API sync instead of this deprecated function
#     return redirect(url_for("home"))

from flask import jsonify

@app.route("/api/stock", methods=["GET"])
def stock_api():
    db_path = DB_PATHS.get("DIP")
    if not db_path:
        return jsonify({"error": "Database path not found"}), 500

    ensure_override_table(db_path)
    ensure_brand_margins_table(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Attach RASALKHORE database to get total stock (DIP + RASALKHORE)
    ras_attached = False
    ras_db_path = DB_PATHS.get("RASALKHORE")
    if ras_db_path:
        ras_db_path = os.path.abspath(ras_db_path)
        if os.path.exists(ras_db_path):
            try:
                cur.execute(f'ATTACH DATABASE "{ras_db_path}" AS ras')
                ras_attached = True
            except sqlite3.Error:
                ras_attached = False

    if ras_attached:
        cur.execute("""
            SELECT
                si."ItemCode",
                si."Description",
                si."Manufacturer Name",
                si."Warehouse Code",
                si."Stock Quantity" AS "DIP Stock",
                COALESCE(rsi."Stock Quantity", 0) AS "RAS Stock",
                (COALESCE(si."Stock Quantity", 0) + COALESCE(rsi."Stock Quantity", 0)) AS "Total Stock",
                CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END AS "Selling Price",
                si."CostPrice",
                si."Upc Code"
            FROM stock_items si
            LEFT JOIN ras.stock_items rsi ON rsi."ItemCode" = si."ItemCode"
            LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
            LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
        """)
    else:
        cur.execute("""
            SELECT
                si."ItemCode",
                si."Description",
                si."Manufacturer Name",
                si."Warehouse Code",
                si."Stock Quantity" AS "DIP Stock",
                0 AS "RAS Stock",
                si."Stock Quantity" AS "Total Stock",
                CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END AS "Selling Price",
                si."CostPrice",
                si."Upc Code"
            FROM stock_items si
            LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
            LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
        """)

    rows = cur.fetchall()
    conn.close()

    stock_list = [
        {
            "item_code": row[0],
            "description": row[1],
            "manufacturer": row[2],
            "warehouse": row[3],
            "dip_stock": row[4],
            "ras_stock": row[5],
            "total_stock": row[6],
            "minimum_selling_price": row[7],  # effective price
            "cost_price": row[8],
            "upc_code": row[9]
        }
        for row in rows
    ]
    return jsonify(stock_list)
# put at top if not already imported
from flask import jsonify
import os, sqlite3, traceback

from flask import jsonify
import os, sqlite3, traceback

@app.route("/api/min-price", methods=["POST"])
def update_min_price():
    if "username" not in session:
        return jsonify(ok=False, error="Unauthorized"), 401

    data = request.get_json(silent=True) or {}
    branch = (data.get("branch") or "").strip().upper()
    item_code = (data.get("item_code") or "").strip()
    price_val = data.get("min_price")
    field = (data.get("field") or "cost").strip().lower()  # For ALABAMA: "cost" or "selling"

    if not item_code:
        return jsonify(ok=False, error="Missing item_code"), 400
    try:
        price_val = float(str(price_val).strip())
        if price_val < 0:
            raise ValueError
        # Round to 2 decimal places
        price_val = round(price_val, 2)
    except Exception:
        return jsonify(ok=False, error=f"Invalid price: {price_val!r}"), 400

    # --- Retail branches (AJMAN, NAH, DEIRA, DEIRA2, ABUDHABI, QUSAIS) ---
    if branch in RETAIL_BRANCHES or branch == "ALLSTORES":
        dip_db = DB_PATHS.get("DIP")
        if not dip_db or not os.path.exists(dip_db):
            return jsonify(ok=False, error=f"DIP DB not found: {os.path.abspath(dip_db or '')}"), 500

        try:
            # make sure the retail_overrides table exists
            ensure_retail_override_table(dip_db)

            conn = sqlite3.connect(dip_db)
            cur = conn.cursor()

            # item must exist in DIP stock_items
            cur.execute('SELECT 1 FROM "stock_items" WHERE "ItemCode" = ?', (item_code,))
            if not cur.fetchone():
                return jsonify(ok=False, error=f'Item not found in DIP stock_items: "{item_code}"'), 404

            # Upsert per-branch selling price override
            cur.execute("""
                INSERT INTO retail_overrides (ItemCode, Branch, SellingPriceOverride, edited_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ItemCode, Branch) DO UPDATE SET
                    SellingPriceOverride = excluded.SellingPriceOverride,
                    edited_by = excluded.edited_by,
                    edited_at = datetime('now')
            """, (item_code, branch, price_val, session.get("username", "admin")))
            conn.commit()
        except Exception as e:
            return jsonify(ok=False, error=f"DB error: {e}"), 500
        finally:
            try: conn.close()
            except: pass

        return jsonify(ok=True, item_code=item_code, price=price_val, source="override", branch=branch)

    # --- Standard branches (DIP, RASALKHORE, ALABAMA) ---
    if branch not in ("DIP", "RASALKHORE", "ALABAMA"):
        return jsonify(ok=False, error=f"Branch not editable: {branch!r}"), 400

    db_path = DB_PATHS.get(branch)
    if not db_path or not os.path.exists(db_path):
        return jsonify(ok=False, error=f"DB not found for {branch}: {os.path.abspath(db_path or '')}"), 500

    try:
        ensure_override_table(db_path)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # item must exist (Alabama: check DIP/RAS; others: check local stock_items)
        if branch == "ALABAMA":
            dip_db = DB_PATHS.get("DIP")
            ras_db = DB_PATHS.get("RASALKHORE")
            found = False
            if dip_db and os.path.exists(dip_db):
                dip_cur = sqlite3.connect(dip_db).cursor()
                dip_cur.execute('SELECT 1 FROM "stock_items" WHERE "ItemCode" = ?', (item_code,))
                if dip_cur.fetchone():
                    found = True
                dip_cur.connection.close()
            if not found and ras_db and os.path.exists(ras_db):
                ras_cur = sqlite3.connect(ras_db).cursor()
                ras_cur.execute('SELECT 1 FROM "stock_items" WHERE "ItemCode" = ?', (item_code,))
                if ras_cur.fetchone():
                    found = True
                ras_cur.connection.close()
            if not found:
                return jsonify(ok=False, error=f'Item not found in DIP/RAS: "{item_code}"'), 404
        else:
            cur.execute('SELECT 1 FROM "stock_items" WHERE "ItemCode" = ?', (item_code,))
            if not cur.fetchone():
                return jsonify(ok=False, error=f'Item not found in stock_items: "{item_code}"'), 404

        if branch == "ALABAMA":
            if field == "selling":
                # Upsert SellingPriceOverride for Alabama
                cur.execute("""
                    INSERT INTO price_overrides (ItemCode, SellingPriceOverride, edited_by)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ItemCode) DO UPDATE SET
                        SellingPriceOverride = excluded.SellingPriceOverride,
                        edited_by = excluded.edited_by,
                        edited_at = datetime('now')
                """, (item_code, price_val, session.get("username", "admin")))
            else:
                # Upsert CostPriceOverride for Alabama
                cur.execute("""
                    INSERT INTO price_overrides (ItemCode, CostPriceOverride, edited_by)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ItemCode) DO UPDATE SET
                        CostPriceOverride = excluded.CostPriceOverride,
                        edited_by = excluded.edited_by,
                        edited_at = datetime('now')
                """, (item_code, price_val, session.get("username", "admin")))
        else:
            # Upsert SellingPriceOverride
            cur.execute("""
                INSERT INTO price_overrides (ItemCode, SellingPriceOverride, edited_by)
                VALUES (?, ?, ?)
                ON CONFLICT(ItemCode) DO UPDATE SET
                    SellingPriceOverride = excluded.SellingPriceOverride,
                    edited_by = excluded.edited_by,
                    edited_at = datetime('now')
            """, (item_code, price_val, session.get("username", "admin")))

        conn.commit()
    except Exception as e:
        return jsonify(ok=False, error=f"DB error: {e}"), 500
    finally:
        try: conn.close()
        except: pass

    return jsonify(ok=True, item_code=item_code, price=price_val, source="override", branch=branch)


# API key for PC sync script (change this to a secure random string)
# IMPORTANT: This must match VPS_API_KEY in sync_stock_pc.py
VPS_API_KEY = "rLEkUZQiljwQWPS5ZJ8m6zawpsr9QUvRqYka-hj7fBw"  # For testing. Change to secure random key for production

# Background sync processing - ONE sync at a time to avoid DB locks
_sync_lock = threading.Lock()
_sync_in_progress = {}
_global_sync_lock = threading.Lock()  # Serialize all syncs and cleanup (warehouses 01-08 share DIP DB)
_cleanup_in_progress = False

def _process_sync_in_background(data):
    """Process sync in background thread. Only one sync runs at a time to prevent DB locks."""
    warehouse_code = data.get("warehouse_code", "").strip()
    branch = WAREHOUSE_MAPPING.get(warehouse_code, {}).get("branch", "UNKNOWN")
    
    # CRITICAL: Wait for any other sync to finish (warehouses 01-07 all write to DIP)
    with _global_sync_lock:
        try:
            # Mark sync as in progress
            with _sync_lock:
                _sync_in_progress[warehouse_code] = True
            
            # Process the sync (same logic as before but extracted)
            mapping = WAREHOUSE_MAPPING[warehouse_code]
            branch = mapping["branch"]
            stock_column = mapping["column"]
            
            db_path = DB_PATHS[branch]
            ensure_override_table(db_path)
            if branch == "DIP":
                ensure_retail_override_table(db_path)
            
            # Create stock_items table if it doesn't exist (background sync path doesn't use sync_stock_from_api)
            with get_db_connection(db_path, timeout=10.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
                if not cur.fetchone():
                    if branch == "DIP":
                        cur.execute("""
                            CREATE TABLE stock_items (
                                "ItemCode" TEXT PRIMARY KEY,
                                "Upc Code" TEXT,
                                "Description" TEXT,
                                "Manufacturer Name" TEXT,
                                "Warehouse Code" TEXT,
                                "Stock Quantity" REAL DEFAULT 0,
                                "Free Stock" REAL DEFAULT 0,
                                "Selling Price" REAL DEFAULT 0,
                                "CostPrice" REAL DEFAULT 0,
                                "AJMAN" REAL DEFAULT 0,
                                "NAH" REAL DEFAULT 0,
                                "DEIRA" REAL DEFAULT 0,
                                "DEIRA2" REAL DEFAULT 0,
                                "ABUDHABI" REAL DEFAULT 0,
                                "QUSAIS" REAL DEFAULT 0
                            )
                        """)
                    else:
                        cur.execute("""
                            CREATE TABLE stock_items (
                                "ItemCode" TEXT PRIMARY KEY,
                                "Upc Code" TEXT,
                                "Description" TEXT,
                                "Manufacturer Name" TEXT,
                                "Warehouse Code" TEXT,
                                "Stock Quantity" REAL DEFAULT 0,
                                "Free Stock" REAL DEFAULT 0,
                                "Selling Price" REAL DEFAULT 0,
                                "CostPrice" REAL DEFAULT 0
                            )
                        """)
                    conn.commit()
            
            # Get existing admin price overrides
            existing_overrides = {}
            existing_retail_overrides = {}
            
            with get_db_connection(db_path, timeout=30.0) as conn:
                cur = conn.cursor()
                
                if data.get("keep_admin_prices", True):
                    cur.execute("SELECT ItemCode, SellingPriceOverride FROM price_overrides WHERE SellingPriceOverride IS NOT NULL")
                    for row in cur.fetchall():
                        existing_overrides[row[0]] = row[1]
                    
                    if branch == "DIP":
                        cur.execute("SELECT ItemCode, Branch, SellingPriceOverride FROM retail_overrides WHERE SellingPriceOverride IS NOT NULL")
                        for row in cur.fetchall():
                            item_code, retail_branch, price = row
                            if item_code not in existing_retail_overrides:
                                existing_retail_overrides[item_code] = {}
                            existing_retail_overrides[item_code][retail_branch] = price
            
            # Load brand margins
            dip_db = DB_PATHS["DIP"]
            ensure_brand_margins_table(dip_db)
            brand_margins = {}
            brand_margins_lower = {}
            default_margin = DEFAULT_MARGIN_PERCENT
            
            use_admin_price_default = True
            brand_use_admin_price = {}
            brand_use_admin_price_lower = {}
            with get_db_connection(dip_db, timeout=10.0) as margin_conn:
                margin_cur = margin_conn.cursor()
                margin_cur.execute("SELECT brand_name, margin_percent, COALESCE(use_admin_price, 1) FROM brand_margins")
                for row in margin_cur.fetchall():
                    if row[0] == "__DEFAULT__":
                        default_margin = row[1]
                        use_admin_price_default = bool(row[2])
                    else:
                        brand_name = row[0]
                        margin = row[1]
                        use_admin = bool(row[2])
                        brand_margins[brand_name] = margin
                        brand_margins_lower[brand_name.lower()] = (brand_name, margin)
                        brand_use_admin_price[brand_name] = use_admin
                        brand_use_admin_price_lower[brand_name.lower()] = use_admin
            
            def get_brand_use_admin_price(manufacturer_name):
                if not manufacturer_name:
                    return use_admin_price_default
                if manufacturer_name in brand_use_admin_price:
                    return brand_use_admin_price[manufacturer_name]
                manufacturer_lower = manufacturer_name.lower()
                if manufacturer_lower in brand_use_admin_price_lower:
                    return brand_use_admin_price_lower[manufacturer_lower]
                return use_admin_price_default
            
            def get_brand_margin_case_insensitive(manufacturer_name):
                if not manufacturer_name:
                    return default_margin
                if manufacturer_name in brand_margins:
                    return brand_margins[manufacturer_name]
                manufacturer_lower = manufacturer_name.lower()
                if manufacturer_lower in brand_margins_lower:
                    return brand_margins_lower[manufacturer_lower][1]
                return default_margin
            
            cost_price_overrides = get_cost_price_overrides(dip_db)
            
            # Get existing items for DIP branch
            existing_items = {}
            if branch == "DIP":
                try:
                    with get_db_connection(db_path, timeout=30.0) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
                        if cur.fetchone():
                            cur.execute('SELECT "ItemCode", "AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS", "Stock Quantity", "Selling Price", "CostPrice", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code", "Free Stock" FROM stock_items')
                            for row in cur.fetchall():
                                existing_items[row[0]] = {
                                    "AJMAN": row[1] or 0, "NAH": row[2] or 0, "DEIRA": row[3] or 0,
                                    "DEIRA2": row[4] or 0, "ABUDHABI": row[5] or 0, "QUSAIS": row[6] or 0,
                                    "Stock Quantity": row[7] or 0, "Selling Price": row[8] or 0,
                                    "CostPrice": row[9] or 0, "Upc Code": row[10] or "",
                                    "Description": row[11] or "", "Manufacturer Name": row[12] or "",
                                    "Warehouse Code": row[13] or "", "Free Stock": row[14] or 0,
                                }
                except sqlite3.Error:
                    existing_items = {}
            
            items = data.get("items", [])
            items_to_insert = []
            
            for item in items:
                item_code = str(item.get("ItemCode", "")).strip()
                if not item_code:
                    continue
                
                upc_code = str(item.get("U_UPCCODE", "")).strip()
                description = str(item.get("ItemName", "")).strip()
                manufacturer = str(item.get("FirmName", "")).strip()
                whs_code = str(item.get("WhsCode", "")).strip()
                on_hand = _to_float(item.get("OnHand", 0), 0.0)
                avg_price = _to_float(item.get("AvgPrice", 0), 0.0)
                
                cost_for_margin = cost_price_overrides.get(item_code, avg_price)
                
                use_admin = get_brand_use_admin_price(manufacturer)
                if data.get("keep_admin_prices", True) and use_admin and item_code in existing_overrides:
                    selling_price = existing_overrides[item_code]
                elif stock_column == "Stock Quantity":
                    margin_percent = get_brand_margin_case_insensitive(manufacturer)
                    margin_divisor = 1 - (margin_percent / 100)
                    if cost_for_margin > 0 and margin_divisor > 0:
                        selling_price = round(cost_for_margin / margin_divisor, 2)
                    else:
                        selling_price = 0.0
                else:
                    selling_price = 0
                
                if branch == "DIP":
                    existing = existing_items.get(item_code, {})
                    final_upc = upc_code if upc_code else existing.get("Upc Code", "")
                    final_description = description if description else existing.get("Description", "")
                    final_manufacturer = manufacturer if manufacturer else existing.get("Manufacturer Name", "")
                    if stock_column == "Stock Quantity":
                        final_whs_code = whs_code if whs_code else existing.get("Warehouse Code", "01")
                    else:
                        final_whs_code = existing.get("Warehouse Code", "01")
                    
                    if item_code in cost_price_overrides:
                        final_cost_price = round(cost_price_overrides[item_code], 2)
                    elif stock_column == "Stock Quantity":
                        final_cost_price = round(avg_price, 2) if avg_price > 0 else round(float(existing.get("CostPrice", 0) or 0), 2)
                    else:
                        existing_cost = existing.get("CostPrice", 0) or 0
                        final_cost_price = round(float(existing_cost), 2)
                    
                    row_data = {
                        "ItemCode": item_code, "Upc Code": final_upc, "Description": final_description,
                        "Manufacturer Name": final_manufacturer, "Warehouse Code": final_whs_code,
                        "Stock Quantity": on_hand if stock_column == "Stock Quantity" else float(existing.get("Stock Quantity", 0) or 0),
                        "Free Stock": float(existing.get("Free Stock", 0) or 0),
                        "Selling Price": round(selling_price, 2) if selling_price > 0 else round(float(existing.get("Selling Price", 0) or 0), 2),
                        "CostPrice": final_cost_price,
                        "AJMAN": on_hand if stock_column == "AJMAN" else float(existing.get("AJMAN", 0) or 0),
                        "NAH": on_hand if stock_column == "NAH" else float(existing.get("NAH", 0) or 0),
                        "DEIRA": on_hand if stock_column == "DEIRA" else float(existing.get("DEIRA", 0) or 0),
                        "DEIRA2": on_hand if stock_column == "DEIRA2" else float(existing.get("DEIRA2", 0) or 0),
                        "ABUDHABI": on_hand if stock_column == "ABUDHABI" else float(existing.get("ABUDHABI", 0) or 0),
                        "QUSAIS": on_hand if stock_column == "QUSAIS" else float(existing.get("QUSAIS", 0) or 0),
                    }
                else:
                    if item_code in cost_price_overrides:
                        ras_cost_price = round(cost_price_overrides[item_code], 2)
                    else:
                        ras_cost_price = round(avg_price, 2)
                    
                    row_data = {
                        "ItemCode": item_code, "Upc Code": upc_code, "Description": description,
                        "Manufacturer Name": manufacturer, "Warehouse Code": whs_code,
                        "Stock Quantity": on_hand, "Free Stock": 0,
                        "Selling Price": round(selling_price, 2), "CostPrice": ras_cost_price,
                    }
                
                items_to_insert.append(row_data)
            
            # Update database incrementally
            if items_to_insert:
                with get_db_connection(db_path, timeout=60.0) as conn:
                    cur = conn.cursor()
                    if branch == "DIP":
                        insert_sql = """
                            INSERT OR REPLACE INTO stock_items (
                                "ItemCode", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code",
                                "Stock Quantity", "Free Stock", "Selling Price", "CostPrice",
                                "AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS"
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """
                        for item in items_to_insert:
                            cur.execute(insert_sql, (
                                item["ItemCode"], item["Upc Code"], item["Description"], 
                                item["Manufacturer Name"], item["Warehouse Code"],
                                item["Stock Quantity"], item["Free Stock"], 
                                item["Selling Price"], item["CostPrice"],
                                item["AJMAN"], item["NAH"], item["DEIRA"], 
                                item["DEIRA2"], item["ABUDHABI"], item["QUSAIS"]
                            ))
                    else:
                        insert_sql = """
                            INSERT OR REPLACE INTO stock_items (
                                "ItemCode", "Upc Code", "Description", "Manufacturer Name", "Warehouse Code",
                                "Stock Quantity", "Free Stock", "Selling Price", "CostPrice"
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """
                        for item in items_to_insert:
                            cur.execute(insert_sql, (
                                item["ItemCode"], item["Upc Code"], item["Description"], 
                                item["Manufacturer Name"], item["Warehouse Code"],
                                item["Stock Quantity"], item["Free Stock"], 
                                item["Selling Price"], item["CostPrice"]
                            ))
            
            ensure_stock_items_indexes(db_path)
            
            # WAL checkpoint: keep WAL file small after bulk writes
            try:
                with get_db_connection(db_path, timeout=10.0) as ckpt_conn:
                    ckpt_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            
            # Broadcast SSE update
            try:
                broadcast_sse_update(branch, {
                    "type": "sync_complete",
                    "warehouse_code": warehouse_code,
                    "branch": branch,
                    "items_updated": len(items_to_insert),
                    "timestamp": datetime.now().isoformat()
                })
            except Exception:
                pass
        
        except Exception as e:
            print(f"Background sync error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            with _sync_lock:
                _sync_in_progress.pop(warehouse_code, None)

@app.route("/settings/sync/ping/", methods=["GET"])
def sync_ping():
    """Simple ping endpoint to test routing and that the app is reachable."""
    return jsonify({"ok": True})

@app.route("/api/sync-stock", methods=["POST"])
def api_sync_stock():
    """
    API endpoint for PC sync script to send stock data to VPS.
    Receives data from PC and updates VPS databases.
    OPTIMIZATION: Returns immediately and processes sync in background thread.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify(success=False, error="No JSON data received"), 400
        
        # Security: Verify API key
        api_key = data.get("api_key")
        if api_key != VPS_API_KEY:
            return jsonify(success=False, error="Invalid API key"), 401
        
        warehouse_code = data.get("warehouse_code", "").strip()
        items = data.get("items", [])
        
        if not warehouse_code or warehouse_code not in WAREHOUSE_MAPPING:
            return jsonify(success=False, error=f"Invalid warehouse code: {warehouse_code}"), 400
        
        if not isinstance(items, list):
            return jsonify(success=False, error="Items must be a list"), 400
        
        # Check if sync is already in progress for this warehouse
        with _sync_lock:
            if warehouse_code in _sync_in_progress:
                return jsonify(success=False, error=f"Sync already in progress for warehouse {warehouse_code}"), 409
        
        # Start background processing
        thread = threading.Thread(target=_process_sync_in_background, args=(data,))
        thread.daemon = True
        thread.start()
        
        # Return immediately
        return jsonify({
            "success": True,
            "message": "Sync started in background",
            "warehouse_code": warehouse_code,
            "items_received": len(items)
        })
        
    except sqlite3.Error as e:
        import traceback
        error_trace = traceback.format_exc()
        return jsonify(success=False, error=f"Database error: {str(e)}", traceback=error_trace), 500
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        return jsonify(success=False, error=f"Unexpected error: {str(e)}", traceback=error_trace), 500



















# Function to initialize (or re-initialize) the database with the Excel data
# def initialize_db(branch, force_update=False):
#     db_path = DB_PATHS[branch]
#     excel_file = f"uploads/stock_data_{branch}.xlsx"

#     if force_update and os.path.exists(db_path):
#         os.remove(db_path)
#         print(f"Existing database for {branch} deleted for update.")

#     if os.path.exists(db_path):
#         print(f"Database for {branch} already exists.")
#         return

#     # ✅ Read Excel ensuring ItemCode is a string
#     df = pd.read_excel(excel_file, dtype={'ItemCode': str})

#     # ✅ Check if ItemCode has NaN values
#     print(f"Before filling NaN - Data from Excel for {branch}:")
#     print(df[['ItemCode', 'Upc Code', 'Description']].head(10))

#     # ✅ Handle missing values
#     df['ItemCode'] = df['ItemCode'].fillna('').astype(str)
#     df['Upc Code'] = df['Upc Code'].fillna('').astype(str)

#     # ✅ Strip column names
#     df.columns = df.columns.str.strip()

#     print(f"After fixing NaN - Data from Excel for {branch}:")
#     print(df[['ItemCode', 'Upc Code', 'Description']].head(10))

#     conn = sqlite3.connect(db_path)
#     cursor = conn.cursor()

#     cursor.execute('''
#         CREATE TABLE IF NOT EXISTS stock_items (
#             "ItemCode" TEXT,
#             "Upc Code" TEXT,
#             "Description" TEXT,
#             "Manufacturer Name" TEXT,
#             "Warehouse Code" TEXT,
#             "Stock Quantity" INTEGER,
#             "Free Stock" INTEGER
#             "Selling Price" INTEGER
#         )
#     ''')

#     df.to_sql('stock_items', conn, if_exists='replace', index=False)

#     # ✅ Check inserted data
#     cursor.execute("SELECT * FROM stock_items LIMIT 10")
#     print("Database Sample Data:", cursor.fetchall())

#     conn.commit()
#     conn.close()

# initialize_db("headoffice")
# initialize_db("rasalkhor")

# # Initialize databases for all branches at startup
# # initialize_db("headoffice")
# # initialize_db("rasalkhor")



# if __name__ == "__main__":
#     app.run(host='0.0.0.0', port=5000)





@app.route("/ajman", methods=["GET","POST"])
def ajman():
    return retail_page("AJMAN")

@app.route("/nah", methods=["GET","POST"])
def nah():
    return retail_page("NAH")

@app.route("/deira", methods=["GET","POST"])
def deira():
    return retail_page("DEIRA")

@app.route("/deira2", methods=["GET","POST"])
def deira2():
    return retail_page("DEIRA2")

@app.route("/abudhabi", methods=["GET","POST"])
def abudhabi():
    return retail_page("ABUDHABI")

@app.route("/qusais", methods=["GET","POST"])
def qusais():
    return retail_page("QUSAIS")



def retail_page(retail_branch):
    results = None
    query = ""
    hide_zero_stock = False

    if request.method == "POST":
        query = request.form.get("query", "").strip().lower()
        hide_zero_stock = request.form.get("hideZeroStock") == "on"

        if query:
            db_path = DB_PATHS["DIP"]
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            ensure_retail_override_table(db_path)

            ensure_brand_margins_table(db_path)
            words = query.split()
            sql = f"""
                SELECT
                    si."ItemCode",
                    si."Upc Code",
                    si."Description",
                    si."Manufacturer Name",
                    si."Warehouse Code",
                    COALESCE(si."{retail_branch}", 0) AS "RetailStock",
                    0 AS "Free Stock",
                    CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(ro.SellingPriceOverride, si."Selling Price") END AS "Selling Price",
                    si."CostPrice"
                FROM stock_items si
                LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
                LEFT JOIN retail_overrides ro
                    ON ro.ItemCode = si."ItemCode" AND ro.Branch = ?
                WHERE
            """
            col_item = 'si."ItemCode"'
            col_upc  = 'si."Upc Code"'
            col_desc = 'si."Description"'
            col_mfg  = 'si."Manufacturer Name"'

            conds, params = [], [retail_branch]
            for w in words:
                like = f"%{w}%"
                conds.append(f"""(
                    LOWER({col_item}) LIKE ? OR
                    LOWER({col_upc})  LIKE ? OR
                    LOWER({col_desc}) LIKE ? OR
                    LOWER({col_mfg})  LIKE ?
                )""")
                params.extend([like, like, like, like])

            sql += " AND ".join(conds)

            if hide_zero_stock:
                sql += f' AND COALESCE(si."{retail_branch}", 0) > 0'

            cur.execute(sql, params)
            results = cur.fetchall()
            conn.close()

    # ---- filtered totals from search results (only when logged in) ----
    branch_total_value = None
    matched_count = 0
    if "username" in session and results:
        matched_count = len(results)
        try:
            # 5 = RetailStock, 8 = Cost
            branch_total_value = round(sum(float(r[5] or 0) * float(r[8] or 0) for r in results), 2)
        except Exception:
            branch_total_value = 0.0

    ctx = {
        "results": results,
        "query": query,
        "hide_zero_stock": hide_zero_stock,
        "hide_zero_cost": False,
        "branch": retail_branch,
        "branch_total_value": branch_total_value,
        "matched_count": matched_count,
    }

    # Optional: expose a branch-specific key (e.g., ajman_total_value)
    if branch_total_value is not None:
        ctx[f"{retail_branch.lower()}_total_value"] = branch_total_value

    # Support partial rendering for AJAX
    if request.args.get("partial") == "1" or request.headers.get("X-Partial") == "1":
        return render_template("_stock_results.html", **ctx)

    return render_template("stock.html", **ctx)


@app.template_filter("money")
def money(v):
    try:
        return f"{float(v or 0):,.2f}"
    except Exception:
        return "0.00"
    

# In app.py

@app.route("/allstores", methods=["GET", "POST"])
def allstores():
    results = None
    query = ""
    hide_zero_stock = False

    if request.method == "POST":
        query = (request.form.get("query") or "").strip().lower()
        hide_zero_stock = request.form.get("hideZeroStock") == "on"

        words = [w for w in query.split() if w]
        where_sql = "1=1"
        params = []
        if words:
            parts = []
            for w in words:
                wlike = f"%{w}%"
                parts.append(
                    """(
                        LOWER(si."ItemCode") LIKE ? OR
                        LOWER(si."Upc Code") LIKE ? OR
                        LOWER(si."Description") LIKE ? OR
                        LOWER(si."Manufacturer Name") LIKE ?
                    )"""
                )
                params.extend([wlike, wlike, wlike, wlike])
            where_sql = " AND ".join(parts)

        dip_db = DB_PATHS["DIP"]
        ras_db_path = os.path.abspath(DB_PATHS["RASALKHORE"])

        # 1. Ensure tables exist before querying
        ensure_override_table(dip_db)
        ensure_retail_override_table(dip_db)

        conn = sqlite3.connect(dip_db)
        cur = conn.cursor()

        cur.execute(f"ATTACH DATABASE '{ras_db_path}' AS ras")

        # 2. UPDATED SQL: Added TRIM() in the LEFT JOIN condition
        ensure_brand_margins_table(dip_db)
        sql = f"""
            SELECT
              si."ItemCode",
              si."Upc Code",
              si."Description",
              COALESCE(si."AJMAN", 0),
              COALESCE(si."NAH", 0),
              COALESCE(si."DEIRA", 0),
              COALESCE(si."DEIRA2", 0),
              COALESCE(si."ABUDHABI", 0),
              COALESCE(si."QUSAIS", 0),
              COALESCE(rsi."Stock Quantity", 0) AS RAS_Stock,
              (
                COALESCE(si."AJMAN", 0) + 
                COALESCE(si."NAH", 0) +
                COALESCE(si."DEIRA", 0) + 
                COALESCE(si."DEIRA2", 0) +
                COALESCE(si."ABUDHABI", 0) +
                COALESCE(si."QUSAIS", 0) +
                COALESCE(rsi."Stock Quantity", 0)
              ) AS TotalStock,
              ROUND((CASE WHEN COALESCE(bm.use_admin_price, 1) = 0 AND (1 - COALESCE(bm.margin_percent, 15)/100) > 0 AND CAST(COALESCE(si."CostPrice", 0) AS REAL) > 0
     THEN ROUND(CAST(si."CostPrice" AS REAL) / (1 - COALESCE(bm.margin_percent, 15)/100), 2)
     WHEN COALESCE(bm.use_admin_price, 1) = 0 THEN si."Selling Price"
     ELSE COALESCE(po.SellingPriceOverride, si."Selling Price") END) * 1.05, 2) AS MinPrice,
              COALESCE(si."CostPrice", 0) AS CostPrice,
              CASE
                WHEN LOWER(si."Manufacturer Name") LIKE 'ariston%'
                THEN COALESCE(si."CostPrice", 0)
                ELSE (COALESCE(si."CostPrice", 0) * 1.03)
              END AS "CostPrice 2"
            FROM stock_items si
            LEFT JOIN ras.stock_items rsi ON rsi."ItemCode" = si."ItemCode"
            LEFT JOIN brand_margins bm ON LOWER(TRIM(bm.brand_name)) = LOWER(TRIM(si."Manufacturer Name"))
            LEFT JOIN price_overrides po ON TRIM(po.ItemCode) = TRIM(si."ItemCode")
            WHERE {where_sql}
            {" AND (" + " + ".join([
                'COALESCE(si."AJMAN", 0)',
                'COALESCE(si."NAH", 0)',
                'COALESCE(si."DEIRA", 0)',
                'COALESCE(si."DEIRA2", 0)',
                'COALESCE(si."ABUDHABI", 0)',
                'COALESCE(si."QUSAIS", 0)',
                'COALESCE(rsi."Stock Quantity", 0)' 
            ]) + ") > 0" if hide_zero_stock else ""}
            ORDER BY si."ItemCode"
        """

        cur.execute(sql, params)
        results = cur.fetchall()
        
        try:
            cur.execute("DETACH DATABASE ras")
        except:
            pass
            
        conn.close()

    # Calculate totals for logged-in users
    branch_totals = None
    matched_count = 0
    if "username" in session and results:
        matched_count = len(results)
        try:
            branch_totals = {
                "AJMAN": 0.0, "NAH": 0.0, "DEIRA": 0.0, 
                "DEIRA2": 0.0, "ABUDHABI": 0.0, "QUSAIS": 0.0, "RAS": 0.0
            }
            # Calculate branch totals from results
            # Results format: ItemCode, UPC, Desc, AJMAN, NAH, DEIRA, DEIRA2, ABUDHABI, QUSAIS, RAS, TotalStock, MinPrice, CostPrice, CostPrice2
            for r in results:
                cost = float(r[12] or 0)  # CostPrice at index 12
                branch_totals["AJMAN"] += float(r[3] or 0) * cost
                branch_totals["NAH"] += float(r[4] or 0) * cost
                branch_totals["DEIRA"] += float(r[5] or 0) * cost
                branch_totals["DEIRA2"] += float(r[6] or 0) * cost
                branch_totals["ABUDHABI"] += float(r[7] or 0) * cost
                branch_totals["QUSAIS"] += float(r[8] or 0) * cost
                branch_totals["RAS"] += float(r[9] or 0) * cost  # RAS stock at index 9
            
            # Round all totals
            for k in branch_totals:
                branch_totals[k] = round(branch_totals[k], 2)
        except Exception:
            branch_totals = None

    ctx = {
        "results": results,
        "query": query,
        "hide_zero_stock": hide_zero_stock,
        "hide_zero_cost": False,
        "branch": "ALLSTORES",
        "branch_totals": branch_totals,
        "matched_count": matched_count,
        "dip_total_value": None,
        "ras_total_value": None,
    }

    # Support partial rendering for AJAX
    if request.args.get("partial") == "1" or request.headers.get("X-Partial") == "1":
        return render_template("_stock_results.html", **ctx)

    return render_template("stock.html", **ctx)

from flask import Response
@app.route('/logo_proxy')
def logo_proxy():
    # Backend fetches the image (bypassing browser security)
    img_url = "https://junaidworld.com/wp-content/uploads/2023/09/footer-logo.png"
    try:
        r = requests.get(img_url, timeout=5)
        return Response(r.content, mimetype='image/png')
    except Exception as e:
        return "", 404
    






@app.route("/register-device", methods=["GET", "POST"])
def register_device():
    # If already approved, go home
    token = request.cookies.get('device_token')
    if token:
        conn = sqlite3.connect(DEVICE_DB)
        c = conn.cursor()
        c.execute("SELECT status FROM trusted_devices WHERE token = ?", (token,))
        row = c.fetchone()
        conn.close()
        if row and row[0] == 'approved':
            return redirect(url_for('home'))
        if row and row[0] == 'pending':
            return redirect(url_for('device_pending'))

    if request.method == "POST":
        device_name = request.form.get("device_name", "Unknown Device")
        new_token = str(uuid.uuid4())
        ip_address = request.remote_addr
        
        # Save to DB
        conn = sqlite3.connect(DEVICE_DB)
        c = conn.cursor()
        c.execute("INSERT INTO trusted_devices (token, device_name, ip_address, created_at) VALUES (?, ?, ?, ?)",
                  (new_token, device_name, ip_address, datetime.now()))
        conn.commit()
        conn.close()

        # Set Cookie for 10 years
        resp = redirect(url_for('device_pending'))
        resp.set_cookie('device_token', new_token, max_age=60*60*24*365*10, httponly=True)
        return resp

    user_agent = request.headers.get('User-Agent')
    return render_template("register_device.html", user_agent=user_agent)


@app.route("/device-pending")
def device_pending():
    # Logic to auto-redirect if approved (like refresh check)
    token = request.cookies.get('device_token')
    if token:
        conn = sqlite3.connect(DEVICE_DB)
        c = conn.cursor()
        c.execute("SELECT status FROM trusted_devices WHERE token = ?", (token,))
        row = c.fetchone()
        conn.close()
        if row and row[0] == 'approved':
            # Update cache so middleware allows the redirect to home
            _device_token_cache[token] = {"status": "approved", "ts": time.time()}
            return redirect(url_for('home'))

    return render_template("device_pending.html")


# --- ADMIN PANEL TO MANAGE DEVICES ---
@app.route("/admin/devices", methods=["GET", "POST"])
def approve_devices():
    # Only allow logged-in Admins
    if "username" not in session:
        flash("Please login to manage devices", "danger")
        return redirect(url_for('login'))

    conn = sqlite3.connect(DEVICE_DB)
    c = conn.cursor()

    # Handle Approval / Deletion
    if request.method == "POST":
        action = request.form.get("action")
        token_to_act = request.form.get("token")
        
        if action == "approve":
            c.execute("UPDATE trusted_devices SET status='approved' WHERE token=?", (token_to_act,))
            flash("Device approved!", "success")
        elif action == "delete":
            c.execute("DELETE FROM trusted_devices WHERE token=?", (token_to_act,))
            flash("Device removed.", "warning")
        if token_to_act and token_to_act in _device_token_cache:
            del _device_token_cache[token_to_act]
        conn.commit()

    # Get List
    c.execute("SELECT * FROM trusted_devices ORDER BY created_at DESC")
    devices = c.fetchall()
    conn.close()

    return render_template("admin_devices.html", devices=devices)


# --- ADMIN PANEL TO MANAGE BRAND MARGINS ---
@app.route("/admin/brand-margins", methods=["GET", "POST"])
def admin_brand_margins():
    """Admin page to manage brand/manufacturer margin percentages."""
    if "username" not in session:
        flash("Please login to manage brand margins", "danger")
        return redirect(url_for('login'))
    
    db_path = DB_PATHS["DIP"]
    ensure_brand_margins_table(db_path)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    search_query = request.args.get("q", "").strip()
    message = None
    message_type = None
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "update_default":
            # Update default margin (use_admin_price is updated via API toggle)
            try:
                new_default = float(request.form.get("default_margin", 15.0))
                cur.execute("""
                    UPDATE brand_margins 
                    SET margin_percent = ?, edited_by = ?, edited_at = datetime('now')
                    WHERE brand_name = '__DEFAULT__'
                """, (new_default, session.get("username", "admin")))
                conn.commit()
                message = f"Default margin updated to {new_default}%"
                message_type = "success"
            except ValueError:
                message = "Invalid margin value"
                message_type = "danger"
        
        elif action == "update_brand":
            # Update specific brand margin
            brand_name = request.form.get("brand_name", "").strip()
            try:
                margin = float(request.form.get("margin_percent", 15.0))
                if brand_name:
                    cur.execute("""
                        INSERT INTO brand_margins (brand_name, margin_percent, edited_by)
                        VALUES (?, ?, ?)
                        ON CONFLICT(brand_name) DO UPDATE SET
                            margin_percent = excluded.margin_percent,
                            edited_by = excluded.edited_by,
                            edited_at = datetime('now')
                    """, (brand_name, margin, session.get("username", "admin")))
                    conn.commit()
                    message = f"Margin for '{brand_name}' updated to {margin}%"
                    message_type = "success"
            except ValueError:
                message = "Invalid margin value"
                message_type = "danger"
        
        elif action == "delete_brand":
            # Remove brand-specific margin (will fall back to default)
            brand_name = request.form.get("brand_name", "").strip()
            if brand_name and brand_name != "__DEFAULT__":
                cur.execute("DELETE FROM brand_margins WHERE brand_name = ?", (brand_name,))
                conn.commit()
                message = f"Margin for '{brand_name}' removed (will use default)"
                message_type = "warning"
        
        elif action == "import_excel":
            # Import margins from Excel
            if "excel_file" not in request.files:
                message = "No file uploaded"
                message_type = "danger"
            else:
                file = request.files["excel_file"]
                if file.filename == "":
                    message = "No file selected"
                    message_type = "danger"
                elif file and file.filename.endswith(('.xlsx', '.xls')):
                    try:
                        df = pd.read_excel(file)
                        # Expected columns: brand_name (or Brand Name), margin_percent (or Margin %)
                        # Normalize column names
                        df.columns = df.columns.str.strip().str.lower()
                        
                        # Find brand name column
                        brand_col = None
                        for col in ['brand_name', 'brand name', 'manufacturer', 'manufacturer name', 'brand']:
                            if col in df.columns:
                                brand_col = col
                                break
                        
                        # Find margin column
                        margin_col = None
                        for col in ['margin_percent', 'margin %', 'margin', 'margin_percentage', 'percentage']:
                            if col in df.columns:
                                margin_col = col
                                break
                        
                        if not brand_col or not margin_col:
                            message = f"Excel must have columns: 'Brand Name' and 'Margin %'. Found: {list(df.columns)}"
                            message_type = "danger"
                        else:
                            imported = 0
                            for _, row in df.iterrows():
                                brand = str(row[brand_col]).strip()
                                try:
                                    margin = float(row[margin_col])
                                    if brand and brand.lower() not in ['nan', 'none', '']:
                                        cur.execute("""
                                            INSERT INTO brand_margins (brand_name, margin_percent, edited_by)
                                            VALUES (?, ?, ?)
                                            ON CONFLICT(brand_name) DO UPDATE SET
                                                margin_percent = excluded.margin_percent,
                                                edited_by = excluded.edited_by,
                                                edited_at = datetime('now')
                                        """, (brand, margin, session.get("username", "admin")))
                                        imported += 1
                                except (ValueError, TypeError):
                                    continue
                            conn.commit()
                            message = f"Imported {imported} brand margins from Excel"
                            message_type = "success"
                    except Exception as e:
                        message = f"Error reading Excel: {str(e)}"
                        message_type = "danger"
                else:
                    message = "Please upload an Excel file (.xlsx or .xls)"
                    message_type = "danger"
    
    # Get default margin and use_admin_price
    cur.execute("SELECT margin_percent, COALESCE(use_admin_price, 1) FROM brand_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    default_margin = row[0] if row else 15.0
    default_use_admin_price = bool(row[1]) if row else True
    
    # Get all unique manufacturers from stock_items
    cur.execute('SELECT DISTINCT "Manufacturer Name" FROM stock_items WHERE "Manufacturer Name" IS NOT NULL AND "Manufacturer Name" != "" ORDER BY "Manufacturer Name"')
    all_manufacturers = [row[0] for row in cur.fetchall()]
    all_manufacturers = [m for m in all_manufacturers if (m or "").strip().upper() not in HIDDEN_BRANDS]
    
    # Get all brand margins (excluding default)
    cur.execute("SELECT brand_name, margin_percent, COALESCE(use_admin_price, 1), edited_by, edited_at FROM brand_margins WHERE brand_name != '__DEFAULT__' ORDER BY brand_name")
    brand_margins = cur.fetchall()
    brand_margins_dict = {row[0]: {"margin": row[1], "use_admin_price": bool(row[2]), "edited_by": row[3], "edited_at": row[4]} for row in brand_margins}
    
    # Build list of all brands with their margins
    brands_list = []
    for mfg in all_manufacturers:
        if mfg in brand_margins_dict:
            brands_list.append({
                "name": mfg,
                "margin": brand_margins_dict[mfg]["margin"],
                "use_admin_price": brand_margins_dict[mfg]["use_admin_price"],
                "is_custom": True,
                "edited_by": brand_margins_dict[mfg]["edited_by"],
                "edited_at": brand_margins_dict[mfg]["edited_at"]
            })
        else:
            brands_list.append({
                "name": mfg,
                "margin": default_margin,
                "use_admin_price": default_use_admin_price,
                "is_custom": False,
                "edited_by": None,
                "edited_at": None
            })
    
    # Filter by search query
    if search_query:
        brands_list = [b for b in brands_list if search_query.lower() in b["name"].lower()]
    
    conn.close()
    
    return render_template("admin_brand_margins.html",
                         brands=brands_list,
                         default_margin=default_margin,
                         default_use_admin_price=default_use_admin_price,
                         search_query=search_query,
                         message=message,
                         message_type=message_type,
                         total_brands=len(all_manufacturers))


@app.route("/api/brand-margins", methods=["GET"])
def api_get_brand_margins():
    """API endpoint to get all brand margins as {BRAND: margin_percentage}. Public, no auth required."""
    db_path = DB_PATHS.get("DIP")
    if not db_path:
        return jsonify({"error": "Database path not found"}), 500
    ensure_brand_margins_table(db_path)
    with get_db_connection(db_path, timeout=10.0) as conn:
        cur = conn.cursor()
        cur.execute("SELECT margin_percent FROM brand_margins WHERE brand_name = '__DEFAULT__'")
        row = cur.fetchone()
        default_margin = float(row[0]) if row else 15.0
        cur.execute("SELECT brand_name, margin_percent FROM brand_margins WHERE brand_name != '__DEFAULT__'")
        custom_margins = {row[0]: float(row[1]) for row in cur.fetchall()}
        cur.execute('SELECT DISTINCT "Manufacturer Name" FROM stock_items WHERE "Manufacturer Name" IS NOT NULL AND "Manufacturer Name" != ""')
        all_manufacturers = [row[0] for row in cur.fetchall()]
        all_manufacturers = [m for m in all_manufacturers if (m or "").strip().upper() not in HIDDEN_BRANDS]
        result = {}
        for mfg in all_manufacturers:
            result[mfg] = custom_margins.get(mfg, default_margin)
    return jsonify(result)


@app.route("/api/brand-use-admin-price", methods=["POST"])
def api_update_brand_use_admin_price():
    """API endpoint to toggle use_admin_price for a brand. Default: use admin-edited price. OFF: use brand margin."""
    if "username" not in session:
        return jsonify(ok=False, error="Unauthorized"), 401
    data = request.get_json(silent=True) or {}
    brand_name = (data.get("brand_name") or "").strip()
    value = data.get("use_admin_price")
    if not brand_name:
        return jsonify(ok=False, error="Missing brand name"), 400
    try:
        use_admin = bool(value)
    except (ValueError, TypeError):
        return jsonify(ok=False, error="Invalid value"), 400
    db_path = DB_PATHS["DIP"]
    ensure_brand_margins_table(db_path)
    with get_db_connection(db_path, timeout=10.0) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO brand_margins (brand_name, margin_percent, use_admin_price, edited_by)
            VALUES (?, 15.0, ?, ?)
            ON CONFLICT(brand_name) DO UPDATE SET
                use_admin_price = ?,
                edited_by = ?,
                edited_at = datetime('now')
        """, (brand_name, 1 if use_admin else 0, session.get("username", "admin"),
              1 if use_admin else 0, session.get("username", "admin")))
    return jsonify(ok=True, brand_name=brand_name, use_admin_price=use_admin)


@app.route("/api/brand-margin", methods=["POST"])
def api_update_brand_margin():
    """API endpoint to update brand margin via AJAX."""
    if "username" not in session:
        return jsonify(ok=False, error="Unauthorized"), 401
    
    data = request.get_json(silent=True) or {}
    brand_name = (data.get("brand_name") or "").strip()
    margin = data.get("margin_percent")
    
    if not brand_name:
        return jsonify(ok=False, error="Missing brand name"), 400
    
    try:
        margin = float(margin)
        if margin < 0 or margin > 1000:
            raise ValueError("Margin must be between 0 and 1000")
    except (ValueError, TypeError) as e:
        return jsonify(ok=False, error=f"Invalid margin: {e}"), 400
    
    db_path = DB_PATHS["DIP"]
    ensure_brand_margins_table(db_path)
    
    with get_db_connection(db_path, timeout=10.0) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO brand_margins (brand_name, margin_percent, edited_by)
            VALUES (?, ?, ?)
            ON CONFLICT(brand_name) DO UPDATE SET
                margin_percent = excluded.margin_percent,
                edited_by = excluded.edited_by,
                edited_at = datetime('now')
        """, (brand_name, margin, session.get("username", "admin")))
    
    return jsonify(ok=True, brand_name=brand_name, margin_percent=margin)


@app.route("/api/alabama-margin", methods=["POST"])
def api_update_alabama_margin():
    """API endpoint to update Alabama margins via AJAX."""
    if "username" not in session:
        return jsonify(ok=False, error="Unauthorized"), 401

    data = request.get_json(silent=True) or {}
    brand_name = (data.get("brand_name") or "").strip()
    cost_margin = data.get("cost_margin_percent")
    brand_margin = data.get("brand_margin_percent")

    if not brand_name:
        return jsonify(ok=False, error="Missing brand name"), 400

    try:
        cost_margin = float(cost_margin)
        brand_margin = float(brand_margin)
        if cost_margin < 0 or cost_margin > 1000:
            raise ValueError("Cost margin must be between 0 and 1000")
        if brand_margin < 0 or brand_margin > 1000:
            raise ValueError("Brand margin must be between 0 and 1000")
    except (ValueError, TypeError) as e:
        return jsonify(ok=False, error=f"Invalid margin: {e}"), 400

    db_path = DB_PATHS["ALABAMA"]
    ensure_alabama_margins_table(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO alabama_margins (brand_name, cost_margin_percent, brand_margin_percent, edited_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(brand_name) DO UPDATE SET
            cost_margin_percent = excluded.cost_margin_percent,
            brand_margin_percent = excluded.brand_margin_percent,
            edited_by = excluded.edited_by,
            edited_at = datetime('now')
        """,
        (brand_name, cost_margin, brand_margin, session.get("username", "admin")),
    )
    conn.commit()
    conn.close()

    return jsonify(
        ok=True,
        brand_name=brand_name,
        cost_margin_percent=cost_margin,
        brand_margin_percent=brand_margin,
    )


# ============================================================================
# Alabama Margins Management (Cost Margin + Brand Margin)
# ============================================================================

@app.route("/admin/alabama-margins", methods=["GET", "POST"])
def admin_alabama_margins():
    """Admin page to manage Alabama-specific margins (cost margin + brand margin)."""
    if "username" not in session:
        flash("Please login to manage Alabama margins", "danger")
        return redirect(url_for('login'))
    
    db_path = DB_PATHS["ALABAMA"]
    ensure_alabama_margins_table(db_path)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    search_query = request.args.get("q", "").strip()
    message = None
    message_type = None
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "update_default":
            # Update default margins
            try:
                cost_margin = float(request.form.get("default_cost_margin", 10.0))
                brand_margin = float(request.form.get("default_brand_margin", 15.0))
                cur.execute("""
                    UPDATE alabama_margins 
                    SET cost_margin_percent = ?, brand_margin_percent = ?, edited_by = ?, edited_at = datetime('now')
                    WHERE brand_name = '__DEFAULT__'
                """, (cost_margin, brand_margin, session.get("username", "admin")))
                conn.commit()
                message = f"Default margins updated: Cost Margin {cost_margin}%, Brand Margin {brand_margin}%"
                message_type = "success"
            except ValueError:
                message = "Invalid margin value"
                message_type = "danger"
        
        elif action == "update_brand":
            # Update specific brand margins
            brand_name = request.form.get("brand_name", "").strip()
            try:
                cost_margin = float(request.form.get("cost_margin_percent", 10.0))
                brand_margin = float(request.form.get("brand_margin_percent", 15.0))
                if brand_name:
                    cur.execute("""
                        INSERT INTO alabama_margins (brand_name, cost_margin_percent, brand_margin_percent, edited_by)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(brand_name) DO UPDATE SET
                            cost_margin_percent = excluded.cost_margin_percent,
                            brand_margin_percent = excluded.brand_margin_percent,
                            edited_by = excluded.edited_by,
                            edited_at = datetime('now')
                    """, (brand_name, cost_margin, brand_margin, session.get("username", "admin")))
                    conn.commit()
                    message = f"Margins for '{brand_name}' updated: Cost {cost_margin}%, Brand {brand_margin}%"
                    message_type = "success"
            except ValueError:
                message = "Invalid margin value"
                message_type = "danger"
        
        elif action == "delete_brand":
            # Remove brand-specific margins (will fall back to default)
            brand_name = request.form.get("brand_name", "").strip()
            if brand_name and brand_name != "__DEFAULT__":
                cur.execute("DELETE FROM alabama_margins WHERE brand_name = ?", (brand_name,))
                conn.commit()
                message = f"Margins for '{brand_name}' removed (will use defaults)"
                message_type = "warning"
        
        elif action == "import_excel":
            # Import margins from Excel
            if "excel_file" not in request.files:
                message = "No file uploaded"
                message_type = "danger"
            else:
                file = request.files["excel_file"]
                if file.filename == "":
                    message = "No file selected"
                    message_type = "danger"
                elif file and file.filename.endswith(('.xlsx', '.xls')):
                    try:
                        df = pd.read_excel(file)
                        df.columns = df.columns.str.strip().str.lower()
                        
                        # Find columns
                        brand_col = None
                        for col in ['brand_name', 'brand name', 'manufacturer', 'manufacturer name', 'brand']:
                            if col in df.columns:
                                brand_col = col
                                break
                        
                        cost_margin_col = None
                        for col in ['cost_margin', 'cost margin', 'cost_margin_percent', 'cost margin %', 'cost margin%']:
                            if col in df.columns:
                                cost_margin_col = col
                                break
                        
                        brand_margin_col = None
                        for col in ['brand_margin', 'brand margin', 'brand_margin_percent', 'brand margin %', 'brand margin%', 'selling_margin', 'selling margin']:
                            if col in df.columns:
                                brand_margin_col = col
                                break
                        
                        if not brand_col or not cost_margin_col or not brand_margin_col:
                            message = f"Excel must have columns: 'Brand Name', 'Cost Margin %', and 'Brand Margin %'. Found: {list(df.columns)}"
                            message_type = "danger"
                        else:
                            imported = 0
                            for _, row in df.iterrows():
                                brand = str(row[brand_col]).strip()
                                try:
                                    cost_margin = float(row[cost_margin_col])
                                    brand_margin = float(row[brand_margin_col])
                                    if brand and brand.lower() not in ['nan', 'none', '']:
                                        cur.execute("""
                                            INSERT INTO alabama_margins (brand_name, cost_margin_percent, brand_margin_percent, edited_by)
                                            VALUES (?, ?, ?, ?)
                                            ON CONFLICT(brand_name) DO UPDATE SET
                                                cost_margin_percent = excluded.cost_margin_percent,
                                                brand_margin_percent = excluded.brand_margin_percent,
                                                edited_by = excluded.edited_by,
                                                edited_at = datetime('now')
                                        """, (brand, cost_margin, brand_margin, session.get("username", "admin")))
                                        imported += 1
                                except (ValueError, TypeError):
                                    continue
                            conn.commit()
                            message = f"Imported {imported} Alabama margins from Excel"
                            message_type = "success"
                    except Exception as e:
                        message = f"Error reading Excel: {str(e)}"
                        message_type = "danger"
                else:
                    message = "Please upload an Excel file (.xlsx or .xls)"
                    message_type = "danger"
    
    # Get default margins
    cur.execute("SELECT cost_margin_percent, brand_margin_percent FROM alabama_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    default_cost_margin = row[0] if row else 10.0
    default_brand_margin = row[1] if row else 15.0
    
    # Get all unique manufacturers from DIP + RASALKHORE databases
    dip_db = DB_PATHS["DIP"]
    ras_db = DB_PATHS["RASALKHORE"]
    
    all_manufacturers = set()
    
    # From DIP
    dip_conn = sqlite3.connect(dip_db)
    dip_cur = dip_conn.cursor()
    dip_cur.execute('SELECT DISTINCT "Manufacturer Name" FROM stock_items WHERE "Manufacturer Name" IS NOT NULL AND "Manufacturer Name" != ""')
    for row in dip_cur.fetchall():
        all_manufacturers.add(row[0])
    dip_conn.close()
    
    # From RASALKHORE
    ras_conn = sqlite3.connect(ras_db)
    ras_cur = ras_conn.cursor()
    ras_cur.execute('SELECT DISTINCT "Manufacturer Name" FROM stock_items WHERE "Manufacturer Name" IS NOT NULL AND "Manufacturer Name" != ""')
    for row in ras_cur.fetchall():
        all_manufacturers.add(row[0])
    ras_conn.close()
    
    all_manufacturers = sorted([m for m in all_manufacturers if (m or "").strip().upper() not in HIDDEN_BRANDS])
    
    # Get all Alabama margins (excluding default)
    cur.execute("SELECT brand_name, cost_margin_percent, brand_margin_percent, edited_by, edited_at FROM alabama_margins WHERE brand_name != '__DEFAULT__' ORDER BY brand_name")
    alabama_margins = cur.fetchall()
    margins_dict = {row[0]: {"cost_margin": row[1], "brand_margin": row[2], "edited_by": row[3], "edited_at": row[4]} for row in alabama_margins}
    
    # Build list of all brands with their margins
    brands_list = []
    for mfg in all_manufacturers:
        if mfg in margins_dict:
            brands_list.append({
                "name": mfg,
                "cost_margin": margins_dict[mfg]["cost_margin"],
                "brand_margin": margins_dict[mfg]["brand_margin"],
                "is_custom": True,
                "edited_by": margins_dict[mfg]["edited_by"],
                "edited_at": margins_dict[mfg]["edited_at"]
            })
        else:
            brands_list.append({
                "name": mfg,
                "cost_margin": default_cost_margin,
                "brand_margin": default_brand_margin,
                "is_custom": False,
                "edited_by": None,
                "edited_at": None
            })
    
    # Filter by search query
    if search_query:
        brands_list = [b for b in brands_list if search_query.lower() in b["name"].lower()]
    
    conn.close()
    
    return render_template("admin_alabama_margins.html",
                         brands=brands_list,
                         default_cost_margin=default_cost_margin,
                         default_brand_margin=default_brand_margin,
                         search_query=search_query,
                         message=message,
                         message_type=message_type,
                         total_brands=len(all_manufacturers))


# ============================================================================
# Cost Price Override System (for brands like COSMO)
# ============================================================================

def ensure_cost_price_overrides_table(db_path: str):
    """Create cost_price_overrides table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cost_price_overrides (
            ItemCode TEXT PRIMARY KEY,
            CostPrice REAL NOT NULL,
            Brand TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def get_cost_price_overrides(db_path: str) -> dict:
    """Get all cost price overrides as a dict {ItemCode: CostPrice}."""
    ensure_cost_price_overrides_table(db_path)
    try:
        with get_db_connection(db_path, timeout=10.0) as conn:
            cur = conn.cursor()
            cur.execute("SELECT ItemCode, CostPrice FROM cost_price_overrides")
            overrides = {row[0]: row[1] for row in cur.fetchall()}
        return overrides
    except sqlite3.OperationalError:
        return {}  # Return empty dict if database is locked


@app.route("/admin/cost-price-overrides", methods=["GET", "POST"])
def admin_cost_price_overrides():
    """Admin page to manage cost price overrides for specific brands."""
    if "username" not in session:
        return redirect(url_for("login"))
    
    db_path = DB_PATHS["DIP"]
    ensure_cost_price_overrides_table(db_path)
    
    message = None
    message_type = None
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "upload_excel":
            # Upload cost prices from Excel
            if "file" not in request.files:
                message = "No file selected"
                message_type = "danger"
            else:
                file = request.files["file"]
                if file.filename == "":
                    message = "No file selected"
                    message_type = "danger"
                elif file and file.filename.endswith(('.xlsx', '.xls')):
                    try:
                        df = pd.read_excel(file)
                        
                        # Expected columns: ItemCode, CostPrice (and optionally Brand)
                        required_cols = ["ItemCode", "CostPrice"]
                        missing_cols = [c for c in required_cols if c not in df.columns]
                        
                        if missing_cols:
                            # Try alternative column names
                            col_mapping = {
                                "Item Code": "ItemCode",
                                "item_code": "ItemCode",
                                "Cost Price": "CostPrice",
                                "cost_price": "CostPrice",
                                "Cost": "CostPrice",
                                "Price": "CostPrice",
                            }
                            for old_name, new_name in col_mapping.items():
                                if old_name in df.columns:
                                    df.rename(columns={old_name: new_name}, inplace=True)
                        
                        if "ItemCode" not in df.columns or "CostPrice" not in df.columns:
                            message = "Excel must have columns: ItemCode, CostPrice"
                            message_type = "danger"
                        else:
                            # Get brand name from form or auto-detect
                            brand_name = request.form.get("brand_name", "").strip()
                            
                            # Insert/update overrides
                            count = 0
                            for _, row in df.iterrows():
                                item_code = str(row.get("ItemCode", "")).strip()
                                try:
                                    cost_price = float(row.get("CostPrice", 0))
                                except (ValueError, TypeError):
                                    continue
                                
                                if item_code and cost_price > 0:
                                    row_brand = row.get("Brand", brand_name) if "Brand" in df.columns else brand_name
                                    cur.execute("""
                                        INSERT INTO cost_price_overrides (ItemCode, CostPrice, Brand, uploaded_by)
                                        VALUES (?, ?, ?, ?)
                                        ON CONFLICT(ItemCode) DO UPDATE SET
                                            CostPrice = excluded.CostPrice,
                                            Brand = excluded.Brand,
                                            uploaded_by = excluded.uploaded_by,
                                            uploaded_at = datetime('now')
                                    """, (item_code, round(cost_price, 2), row_brand, session.get("username", "admin")))
                                    count += 1
                            
                            conn.commit()
                            message = f"Successfully imported {count} cost price overrides"
                            message_type = "success"
                    except Exception as e:
                        message = f"Error reading Excel: {str(e)}"
                        message_type = "danger"
                else:
                    message = "Please upload an Excel file (.xlsx or .xls)"
                    message_type = "danger"
        
        elif action == "delete_override":
            item_code = request.form.get("item_code", "").strip()
            if item_code:
                cur.execute("DELETE FROM cost_price_overrides WHERE ItemCode = ?", (item_code,))
                conn.commit()
                message = f"Cost price override for {item_code} removed"
                message_type = "warning"
        
        elif action == "delete_brand":
            brand_name = request.form.get("brand_name", "").strip()
            if brand_name:
                cur.execute("DELETE FROM cost_price_overrides WHERE Brand = ?", (brand_name,))
                deleted = cur.rowcount
                conn.commit()
                message = f"Removed {deleted} overrides for brand '{brand_name}'"
                message_type = "warning"
        
        elif action == "delete_all":
            cur.execute("DELETE FROM cost_price_overrides")
            deleted = cur.rowcount
            conn.commit()
            message = f"Removed all {deleted} cost price overrides"
            message_type = "warning"
    
    # Get search query
    search_query = request.args.get("search", "").strip().lower()
    
    # Get all overrides
    if search_query:
        cur.execute("""
            SELECT ItemCode, CostPrice, Brand, uploaded_by, uploaded_at
            FROM cost_price_overrides
            WHERE LOWER(ItemCode) LIKE ? OR LOWER(Brand) LIKE ?
            ORDER BY Brand, ItemCode
        """, (f"%{search_query}%", f"%{search_query}%"))
    else:
        cur.execute("""
            SELECT ItemCode, CostPrice, Brand, uploaded_by, uploaded_at
            FROM cost_price_overrides
            ORDER BY Brand, ItemCode
        """)
    
    overrides = cur.fetchall()
    
    # Get stats
    cur.execute("SELECT COUNT(*) FROM cost_price_overrides")
    total_overrides = cur.fetchone()[0]
    
    cur.execute("SELECT Brand, COUNT(*) FROM cost_price_overrides GROUP BY Brand ORDER BY COUNT(*) DESC")
    brand_stats = cur.fetchall()
    
    conn.close()
    
    return render_template("admin_cost_price_overrides.html",
                         overrides=overrides,
                         total_overrides=total_overrides,
                         brand_stats=brand_stats,
                         search_query=search_query,
                         message=message,
                         message_type=message_type)


# ============================================================================
# SSE (Server-Sent Events) Endpoints for Real-Time Updates
# ============================================================================

@app.route("/api/stock-stream/<branch>")
def stock_stream(branch):
    """
    SSE endpoint for real-time stock updates.
    Clients connect here and receive updates when sync completes.
    """
    def event_stream():
        # Create a queue for this connection
        q = queue.Queue()
        
        # Add this connection to the branch's connection list
        with sse_lock:
            if branch not in sse_connections:
                sse_connections[branch] = []
            sse_connections[branch].append(q)
        
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'branch': branch})}\n\n"
            
            # Keep connection alive and send updates
            while True:
                try:
                    # Wait for message (with timeout for keep-alive)
                    message = q.get(timeout=30)
                    yield f"data: {json.dumps(message)}\n\n"
                except queue.Empty:
                    # Send keep-alive ping
                    yield f": keep-alive\n\n"
        except GeneratorExit:
            # Client disconnected, remove from connections
            with sse_lock:
                if branch in sse_connections:
                    try:
                        sse_connections[branch].remove(q)
                    except ValueError:
                        pass
        finally:
            # Cleanup: remove queue from connections
            with sse_lock:
                if branch in sse_connections:
                    try:
                        sse_connections[branch].remove(q)
                    except ValueError:
                        pass
    
    # Create response with proper SSE headers for production
    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # Disable buffering in Nginx
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = '*'  # Adjust if needed for CORS
    return response


@app.route("/api/notify-sync-complete", methods=["POST"])
def notify_sync_complete():
    """
    Endpoint for sync script to notify Flask that sync completed.
    This triggers SSE broadcast to all connected clients.
    """
    try:
        data = request.get_json(silent=True) or {}
        branch = data.get("branch", "").strip()
        warehouse_code = data.get("warehouse_code", "").strip()
        items_updated = data.get("items_updated", 0)
        
        if not branch:
            return jsonify(success=False, error="Branch required"), 400
        
        # Broadcast update to all SSE connections for this branch
        broadcast_sse_update(branch, {
            "type": "sync_complete",
            "warehouse_code": warehouse_code,
            "branch": branch,
            "items_updated": items_updated,
            "timestamp": datetime.now().isoformat()
        })
        
        return jsonify(success=True, message=f"Update broadcasted to {branch}")
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500






if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000 , debug=True)