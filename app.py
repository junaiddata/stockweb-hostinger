from flask import Flask, request, render_template, redirect, url_for
import sqlite3
import pandas as pd
import os
from flask import Flask, request, render_template, redirect, url_for, session, flash, Response
from werkzeug.security import check_password_hash, generate_password_hash
import requests
from datetime import datetime
import uuid
import threading
import queue
import json


DEVICE_DB = "devices.db"

def init_device_db():
    conn = sqlite3.connect(DEVICE_DB)
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
    conn.commit()
    conn.close()


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


@app.before_request
def device_restriction_middleware():
    # 1. Allow Static files (CSS/JS/Images)
    if request.path.startswith('/static'):
        return

    # 2. Allow SSE endpoints (no device check needed)
    if request.path.startswith('/api/stock-stream/'):
        return
    
    # 3. Allow sync notification endpoint (called by sync script)
    if request.path == '/api/notify-sync-complete':
        return

    # 4. Allow Login & Device Registration pages explicitly
    allowed_endpoints = [
        'login',            # Admin login
        'register_device',  # The form
        'device_pending',   # The waiting screen
        'approve_devices',  # The admin panel to approve
        'admin_brand_margins',  # Brand margin management
        'api_update_brand_margin',  # Brand margin API
        'logout',           # Logout
        'logo_proxy',
        'stock_api',        # Logo image
        'api_sync_stock'    # PC sync API endpoint
    ]
    
    if request.endpoint in allowed_endpoints:
        return

    # 3. Check for Cookie
    token = request.cookies.get('device_token')
    
    if not token:
        return redirect(url_for('register_device'))

    # 4. Check Database
    conn = sqlite3.connect(DEVICE_DB)
    c = conn.cursor()
    c.execute("SELECT status FROM trusted_devices WHERE token = ?", (token,))
    row = c.fetchone()
    conn.close()

    # 5. Logic:
    # If no record found -> Re-register
    if not row:
        return redirect(url_for('register_device'))
    
    # If record exists but is Pending -> Wait
    if row[0] != 'approved':
        return redirect(url_for('device_pending'))

    # If Approved -> Access Granted (Do nothing, let Flask continue)

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"xls", "xlsx"}
app.secret_key = "junaid2365"  # Required for session cookies

# Example: hardcoded users (can be moved to DB)
USERS = {
    "admin": generate_password_hash("junaid6231"),  # Hashed password
    "staff": generate_password_hash("staff123")
}



# Define the SQLite database file path
DB_PATHS = {
    "DIP": "stock_data_headoffice.db",
    "RASALKHORE": "stock_data_rasalkhor.db",
    "ALABAMA": "stock_data_alabama.db"
}

# Retail branch names exactly as your OUTPUT_DIP column headers
RETAIL_BRANCHES = ["AJMAN", "NAH", "DEIRA", "DEIRA2", "ABUDHABI", "QUSAIS","ALLSTORES"]


def ensure_retail_override_table(db_path: str):
    """
    Overrides for retail branches live only in the DIP DB.
    Keyed by (ItemCode, Branch). Does NOT affect existing price_overrides tables.
    """
    conn = sqlite3.connect(db_path)
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
    conn.commit()
    conn.close()

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Default margin percentage for all brands (can be changed by admin)
DEFAULT_MARGIN_PERCENT = 15.0

def ensure_brand_margins_table(db_path: str):
    """
    Create brand_margins table for storing margin percentages per brand/manufacturer.
    Also stores the default margin setting.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brand_margins (
            brand_name TEXT PRIMARY KEY,
            margin_percent REAL DEFAULT 15.0,
            edited_by TEXT,
            edited_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Insert default margin row if not exists
    cur.execute("""
        INSERT OR IGNORE INTO brand_margins (brand_name, margin_percent, edited_by)
        VALUES ('__DEFAULT__', 15.0, 'system')
    """)
    conn.commit()
    conn.close()

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
    Pulls from https://do.junaidworld.com/api/items/unique-qty
    """
    url = "https://do.junaidworld.com/api/items/unique-qty"
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

def fetch_sold_breakdown_map():
    """
    Returns: { "ITEMCODE": {"total": float, "ho": float, "others": float}, ... }
    Pulls from your new /api/items/unique-qty endpoint.
    """
    url = "https://do.junaidworld.com/api/items/unique-qty"
    try:
        r = requests.get(url, timeout=5)
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
            }
        return out
    except Exception as e:
        print("Sold breakdown API error:", e)
        return {}
    
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

API_BASE_URL = "http://192.168.1.103/IntegrationApi/api/Stock"

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
        # Call API
        payload = {"Warehouse": warehouse_code, "Active": "Y"}
        response = requests.post(API_BASE_URL, json=payload, timeout=30)
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
                        "ItemCode" TEXT,
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
                        "ItemCode" TEXT,
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
            
            # Calculate selling price: 15% margin using division method
            # 15% margin = Cost / 0.85
            calculated_selling_price = round(avg_price / 0.85, 2) if avg_price > 0 else 0.0
            
            # Use existing override if available, otherwise use calculated price
            if keep_admin_prices and item_code in existing_overrides:
                selling_price = round(existing_overrides[item_code], 2)
            else:
                selling_price = calculated_selling_price
            
            # Build row data
            if branch == "DIP":
                # Get existing data for this item to preserve other columns
                existing = existing_items.get(item_code, {})
                
                # Use API data if available, otherwise fall back to existing data
                final_upc = upc_code if upc_code else existing.get("Upc Code", "")
                final_description = description if description else existing.get("Description", "")
                final_manufacturer = manufacturer if manufacturer else existing.get("Manufacturer Name", "")
                final_whs_code = whs_code if whs_code else existing.get("Warehouse Code", "")
                # Always use AvgPrice from API as cost price (even if 0, it's the actual cost from API)
                # Round to 2 decimal places
                final_cost_price = round(avg_price, 2)
                
                row_data = {
                    "ItemCode": item_code,
                    "Upc Code": final_upc,
                    "Description": final_description,
                    "Manufacturer Name": final_manufacturer,
                    "Warehouse Code": final_whs_code,
                    "Stock Quantity": on_hand if stock_column == "Stock Quantity" else existing.get("Stock Quantity", 0),
                    "Free Stock": existing.get("Free Stock", 0),
                    "Selling Price": round(selling_price, 2) if selling_price > 0 else round(existing.get("Selling Price", 0), 2),
                    "CostPrice": final_cost_price,  # Always use AvgPrice from API (rounded to 2 decimals)
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
        
        # Replace stock_items table with new data
        if items_to_insert:
            # Convert to DataFrame for easier insertion
            import pandas as pd
            df = pd.DataFrame(items_to_insert)
            df.to_sql("stock_items", conn, if_exists="replace", index=False)
        
        conn.commit()
        conn.close()
        
        return True, items_updated, None
        
    except requests.RequestException as e:
        return False, 0, f"API request failed: {str(e)}"
    except sqlite3.Error as e:
        return False, 0, f"Database error: {str(e)}"
    except Exception as e:
        return False, 0, f"Unexpected error: {str(e)}"

def sync_all_warehouses_from_api(keep_admin_prices=True):
    """
    Sync stock data from API for all warehouses (01-08).
    
    Returns:
        dict: Results for each warehouse {"01": (success, count, error), ...}
    """
    results = {}
    for warehouse_code in sorted(WAREHOUSE_MAPPING.keys()):
        success, count, error = sync_stock_from_api(warehouse_code, keep_admin_prices)
        results[warehouse_code] = {
            "success": success,
            "items_updated": count,
            "error": error
        }
    return results

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
    conn = sqlite3.connect(db_path)
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
    conn.commit()
    conn.close()

def update_database(branch, df, keep_admin_prices=True):
    db_path = DB_PATHS[branch]
    conn = sqlite3.connect(db_path)
    df.to_sql("stock_items", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()

    ensure_override_table(db_path)

    # If user UNCHECKED the box, clear overrides for that branch
    if not keep_admin_prices:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM price_overrides")
        conn.commit()
        conn.close()

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
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # make sure overrides table exists for JOINs
            ensure_override_table(db_path)

            # words for filtering
            query_words = query.split()

            # --- Build SELECT per-branch ---
            if branch == "ALABAMA":
                # ALABAMA shows effective CostPrice with override
                sql_query = """
                    SELECT
                        si."ItemCode",
                        si."Upc Code",
                        si."Description",
                        si."Manufacturer Name",
                        COALESCE(po.CostPriceOverride, si."CostPrice") AS "CostPrice"
                    FROM stock_items si
                    LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
                    WHERE
                """
                col_item = 'si."ItemCode"'
                col_upc  = 'si."Upc Code"'
                col_desc = 'si."Description"'
                col_mfg  = 'si."Manufacturer Name"'
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
                            COALESCE(po.SellingPriceOverride, si."Selling Price") AS "Selling Price", -- 8
                            si."CostPrice" ,                                     -- 9
                            (COALESCE(si."Stock Quantity",0) + COALESCE(rsi."Stock Quantity",0)) AS "Total Stock" -- 10
                        FROM stock_items si
                        LEFT JOIN ras.stock_items rsi ON rsi."ItemCode" = si."ItemCode"
                        LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
                        WHERE
                    """
                    col_item = 'si."ItemCode"'
                    col_upc  = 'si."Upc Code"'
                    col_desc = 'si."Description"'
                    col_mfg  = 'si."Manufacturer Name"'
                else:
                    # RASALKHORE page (or any other non-ALABAMA branch)
                    sql_query = """
                        SELECT
                            si."ItemCode",
                            si."Upc Code",
                            si."Description",
                            si."Manufacturer Name",
                            si."Warehouse Code",
                            si."Stock Quantity",
                            si."Free Stock",
                            COALESCE(po.SellingPriceOverride, si."Selling Price") AS "Selling Price",
                            si."CostPrice"
                        FROM stock_items si
                        LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
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
                sql_query += ' AND CAST("CostPrice" AS REAL) > 0'

            # --- Execute ---
            cursor.execute(sql_query, params)
            results = cursor.fetchall()

            # If DIP page, append Sold Stock as last column (index 10)
            if branch == "DIP":
                if session.get("username"):
                    sold_map = fetch_sold_breakdown_map()

                    # <<< TEMP DEBUG: log a few suspicious entries >>>
                    # replace "ITEM_CODE_YOU_SAW_639" with the actual item code from the row
                    dbg_code = "700318"
                    if dbg_code in sold_map:
                        print("DBG sold_map[", dbg_code, "] =", sold_map[dbg_code])
                    else:
                        print("DBG sold_map missing code:", dbg_code)

                    def _g(code, key):
                        return (sold_map.get(code, {}) or {}).get(key, 0.0)

                    results = [
                        row + (
                            _g(str(row[0]).strip(), "total"),
                            _g(str(row[0]).strip(), "ho"),
                            _g(str(row[0]).strip(), "others"),
                        )
                        for row in results
                    ]

            # Detach attached DB (only if we attached it)
            if branch == "DIP":
                try:
                    cursor.execute("DETACH DATABASE ras")
                except Exception:
                    pass

            conn.close()
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
                    placeholders = ",".join(["?"] * len(item_codes))
                    dip_db_path = DB_PATHS["DIP"]
                    conn2 = sqlite3.connect(dip_db_path)
                    cur2 = conn2.cursor()
                    # Pull retail branch stocks + DIP cost for ONLY the matched items
                    cur2.execute(f'''
                        SELECT
                            si."ItemCode",
                            CAST(si."CostPrice" AS REAL)                    AS cost,
                            CAST(COALESCE(si."AJMAN",    0) AS REAL)        AS aj,
                            CAST(COALESCE(si."NAH",      0) AS REAL)        AS nah,
                            CAST(COALESCE(si."DEIRA",    0) AS REAL)        AS deira,
                            CAST(COALESCE(si."DEIRA2",   0) AS REAL)        AS deira2,
                            CAST(COALESCE(si."ABUDHABI", 0) AS REAL)        AS abu,
                            CAST(COALESCE(si."QUSAIS",   0) AS REAL)        AS qus
                        FROM stock_items si
                        WHERE si."ItemCode" IN ({placeholders})
                    ''', item_codes)

                    for _, cost, aj, nah, deira, deira2, abu, qus in cur2.fetchall():
                        c = float(cost or 0)
                        branch_totals["AJMAN"]    += float(aj or 0)    * c
                        branch_totals["NAH"]      += float(nah or 0)   * c
                        branch_totals["DEIRA"]    += float(deira or 0) * c
                        branch_totals["DEIRA2"]   += float(deira2 or 0)* c
                        branch_totals["ABUDHABI"] += float(abu or 0)   * c
                        branch_totals["QUSAIS"]   += float(qus or 0)   * c

                    conn2.close()

                    # Round all totals
                    for k in list(branch_totals.keys()):
                        branch_totals[k] = round(branch_totals[k] or 0.0, 2)

            elif branch == "RASALKHORE":
                # 5 = RAS Stock, 8 = Cost
                ras_total_value = round(sum(float(r[5] or 0) * float(r[8] or 0) for r in results), 2)

                # For DIP value, only for matched items
                item_codes = [str(r[0]).strip() for r in results if r and r[0]]
                dip_total_value = 0.0
                if item_codes:
                    placeholders = ",".join(["?"] * len(item_codes))
                    dip_db_path = DB_PATHS["DIP"]
                    conn2 = sqlite3.connect(dip_db_path)
                    cur2 = conn2.cursor()
                    cur2.execute(f'''
                        SELECT
                            CAST(si."Stock Quantity" AS REAL) AS dip_stock,
                            CAST(si."CostPrice"     AS REAL)  AS cost
                        FROM stock_items si
                        WHERE si."ItemCode" IN ({placeholders})
                    ''', item_codes)
                    for dip_stock, cost in cur2.fetchall():
                        dip_total_value += float(dip_stock or 0) * float(cost or 0)
                    conn2.close()
                    dip_total_value = round(dip_total_value, 2)

        except Exception:
            dip_total_value = 0.0 if dip_total_value is None else dip_total_value
            ras_total_value = 0.0 if ras_total_value is None else ras_total_value

    return render_template(
        "stock.html",
        results=results,
        query=query,
        hide_zero_stock=hide_zero_stock,
        hide_zero_cost=hide_zero_cost,
        branch=branch,
        dip_total_value=dip_total_value,
        ras_total_value=ras_total_value,
        matched_count=matched_count,
        branch_totals=branch_totals,  # NEW
    )
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
                
                -- PRICE LOGIC: 
                -- 1. Check for specific 'ALLSTORES' override
                -- 2. Check for general 'Admin/DIP' override
                -- 3. Fallback to Excel Selling Price
                COALESCE(ro.SellingPriceOverride, po.SellingPriceOverride, si."Selling Price", 0) AS MinPrice, -- 13
                
                si."CostPrice"              -- 14
            FROM stock_items si
            LEFT JOIN ras.stock_items rsi ON TRIM(rsi."ItemCode") = TRIM(si."ItemCode")
            
            -- Join specific Retail Override (AllStores)
            LEFT JOIN retail_overrides ro 
                ON TRIM(ro.ItemCode) = TRIM(si."ItemCode") 
                AND ro.Branch = 'ALLSTORES'

            -- Join generic Admin Override (DIP)
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
        
        cur.execute(f"""
            SELECT
                si."ItemCode",
                si."Upc Code",
                si."Description",
                si."Manufacturer Name",
                si."Warehouse Code",
                COALESCE(si."{branch}", 0) AS retail_stock,
                0 AS free_stock,
                COALESCE(ro.SellingPriceOverride, si."Selling Price") AS eff_min_price,
                si."CostPrice"
            FROM stock_items si
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
        db_path = DB_PATHS[branch]
        ensure_override_table(db_path)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT "ItemCode","Upc Code","Description","Manufacturer Name",
            COALESCE(po.CostPriceOverride, "CostPrice") 
            FROM stock_items 
            LEFT JOIN price_overrides po ON po.ItemCode = stock_items.ItemCode
            WHERE "ItemCode" = ?
        """, (item_code,))
        item = cur.fetchone()
        conn.close()

        if item:
            item_data = {
                "ItemCode": item[0], "UpcCode": item[1], "Description": item[2],
                "ManufacturerName": item[3], "WarehouseCode": None,
                "StockQuantity": None, "FreeStock": None, "MinSellingPrice": None,
                "CostPrice": item[4] if "username" in session else None,
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
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            si."ItemCode", si."Upc Code", si."Description", si."Manufacturer Name", si."Warehouse Code",
            si."Stock Quantity", si."Free Stock",
            COALESCE(po.SellingPriceOverride, si."Selling Price") AS "Selling Price",
            si."CostPrice"
        FROM stock_items si
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
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            si."ItemCode",
            si."Description",
            si."Manufacturer Name",
            si."Warehouse Code",
            si."Stock Quantity",
            COALESCE(po.SellingPriceOverride, si."Selling Price") AS "Selling Price",
            si."CostPrice",
            si."Upc Code"
        FROM stock_items si
        LEFT JOIN price_overrides po ON po.ItemCode = si.ItemCode
    """)

    rows = cur.fetchall()
    conn.close()

    stock_list = [
        {
            "item_code": row[0],
            "description": row[1],
            "manufacturer": row[2],
            "warehouse": row[3],
            "stock_quantity": row[4],
            "minimum_selling_price": row[5],  # effective price
            "cost_price": row[6],
            "upc_code": row[7]
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

        # item must exist in sheet
        cur.execute('SELECT 1 FROM "stock_items" WHERE "ItemCode" = ?', (item_code,))
        if not cur.fetchone():
            return jsonify(ok=False, error=f'Item not found in stock_items: "{item_code}"'), 404

        if branch == "ALABAMA":
            # Upsert CostPriceOverride
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

@app.route("/api/sync-stock", methods=["POST"])
def api_sync_stock():
    """
    API endpoint for PC sync script to send stock data to VPS.
    Receives data from PC and updates VPS databases.
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
        keep_admin_prices = data.get("keep_admin_prices", True)
        
        if not warehouse_code or warehouse_code not in WAREHOUSE_MAPPING:
            return jsonify(success=False, error=f"Invalid warehouse code: {warehouse_code}"), 400
        
        if not isinstance(items, list):
            return jsonify(success=False, error="Items must be a list"), 400
        
        mapping = WAREHOUSE_MAPPING[warehouse_code]
        branch = mapping["branch"]
        stock_column = mapping["column"]
        
        # Get database path
        db_path = DB_PATHS[branch]
        ensure_override_table(db_path)
        if branch == "DIP":
            ensure_retail_override_table(db_path)
        
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Get existing admin price overrides
        existing_overrides = {}
        existing_retail_overrides = {}
        
        if keep_admin_prices:
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
        
        # Load brand margins for calculating selling prices (always from DIP DB)
        dip_db = DB_PATHS["DIP"]
        ensure_brand_margins_table(dip_db)
        brand_margins = {}  # Case-sensitive lookup: {brand_name: margin}
        brand_margins_lower = {}  # Case-insensitive lookup: {brand_name.lower(): (original_name, margin)}
        default_margin = DEFAULT_MARGIN_PERCENT
        
        # Load margins from DIP DB (central margin storage)
        margin_conn = sqlite3.connect(dip_db)
        margin_cur = margin_conn.cursor()
        margin_cur.execute("SELECT brand_name, margin_percent FROM brand_margins")
        for row in margin_cur.fetchall():
            if row[0] == "__DEFAULT__":
                default_margin = row[1]
            else:
                brand_name = row[0]
                margin = row[1]
                brand_margins[brand_name] = margin
                # Create case-insensitive lookup
                brand_margins_lower[brand_name.lower()] = (brand_name, margin)
        margin_conn.close()
        
        # Helper function for case-insensitive brand margin lookup
        def get_brand_margin_case_insensitive(manufacturer_name):
            """Get brand margin with case-insensitive lookup."""
            if not manufacturer_name:
                return default_margin
            # Try exact match first
            if manufacturer_name in brand_margins:
                return brand_margins[manufacturer_name]
            # Try case-insensitive match
            manufacturer_lower = manufacturer_name.lower()
            if manufacturer_lower in brand_margins_lower:
                return brand_margins_lower[manufacturer_lower][1]
            # Fall back to default
            return default_margin
        
        # Load cost price overrides (for brands like COSMO where API cost is wrong)
        cost_price_overrides = get_cost_price_overrides(dip_db)
        
        # Get existing items for DIP branch to preserve other columns
        existing_items = {}
        if branch == "DIP":
            try:
                # Check if stock_items table exists
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
                if cur.fetchone():
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
            except sqlite3.Error as e:
                # If table doesn't exist or query fails, continue with empty existing_items
                print(f"Warning: Could not read existing items: {e}")
                existing_items = {}
        
        # Process items
        items_to_insert = []
        
        for item in items:
            item_code = str(item.get("ItemCode", "")).strip()
            if not item_code:
                continue
            
            # Transform PC data to database format
            upc_code = str(item.get("U_UPCCODE", "")).strip()
            description = str(item.get("ItemName", "")).strip()
            manufacturer = str(item.get("FirmName", "")).strip()
            whs_code = str(item.get("WhsCode", "")).strip()
            on_hand = _to_float(item.get("OnHand", 0), 0.0)
            avg_price = _to_float(item.get("AvgPrice", 0), 0.0)  # Cost price from API
            
            # For selling price:
            # - Use admin override if exists (admin edits are NOT affected by brand margins)
            # - Otherwise calculate margin based on brand/manufacturer
            # - For retail warehouses (02-07), preserve existing selling price
            
            # Determine the cost price to use for margin calculation
            # Use override if exists (for brands like COSMO), otherwise use API price
            cost_for_margin = cost_price_overrides.get(item_code, avg_price)
            
            # Check if admin has edited this price
            if keep_admin_prices and item_code in existing_overrides:
                selling_price = existing_overrides[item_code]  # Keep admin-edited price (NOT affected by brand margin)
            elif stock_column == "Stock Quantity":  # Warehouse 01 (DIP) or 08 (RASALKHORE) - calculate brand-specific margin
                # Get margin for this manufacturer/brand (case-insensitive lookup)
                margin_percent = get_brand_margin_case_insensitive(manufacturer)
                # Calculate using division: Cost / (1 - margin/100)
                # Example: 15% margin = Cost / 0.85, 16% margin = Cost / 0.84
                margin_divisor = 1 - (margin_percent / 100)
                
                # Calculate selling price with brand-specific margin (using override cost if exists)
                if cost_for_margin > 0 and margin_divisor > 0:
                    selling_price = round(cost_for_margin / margin_divisor, 2)
                    # Debug: Log when brand margin is different from default (first 5 items only)
                    if margin_percent != default_margin and len([i for i in items_to_insert if i.get("ItemCode") == item_code]) == 0:
                        print(f"[Brand Margin] Item {item_code}: Manufacturer='{manufacturer}', Margin={margin_percent}%, Cost={cost_for_margin}, Selling={selling_price}")
                else:
                    selling_price = 0.0
            else:  # Retail warehouses 02-07 - preserve existing selling price
                selling_price = 0  # Will use existing below
            
            # Build row data
            if branch == "DIP":
                existing = existing_items.get(item_code, {})
                
                final_upc = upc_code if upc_code else existing.get("Upc Code", "")
                final_description = description if description else existing.get("Description", "")
                final_manufacturer = manufacturer if manufacturer else existing.get("Manufacturer Name", "")
                final_whs_code = whs_code if whs_code else existing.get("Warehouse Code", "")
                
                # Cost price: Check for override first (for brands like COSMO)
                # If override exists, use it instead of API price
                # Otherwise, ONLY update from warehouse 01 (main DIP warehouse)
                if item_code in cost_price_overrides:
                    # Use uploaded cost price override (don't take from API)
                    final_cost_price = round(cost_price_overrides[item_code], 2)
                elif stock_column == "Stock Quantity":  # This is warehouse 01
                    final_cost_price = round(avg_price, 2) if avg_price > 0 else round(float(existing.get("CostPrice", 0) or 0), 2)
                else:  # Retail warehouses 02-07 - preserve existing cost price
                    existing_cost = existing.get("CostPrice", 0) or 0
                    final_cost_price = round(float(existing_cost), 2)
                
                row_data = {
                    "ItemCode": item_code,
                    "Upc Code": final_upc,
                    "Description": final_description,
                    "Manufacturer Name": final_manufacturer,
                    "Warehouse Code": final_whs_code,
                    "Stock Quantity": on_hand if stock_column == "Stock Quantity" else float(existing.get("Stock Quantity", 0) or 0),
                    "Free Stock": float(existing.get("Free Stock", 0) or 0),
                    "Selling Price": round(selling_price, 2) if selling_price > 0 else round(float(existing.get("Selling Price", 0) or 0), 2),
                    "CostPrice": final_cost_price,  # Always update from API AvgPrice when available (already rounded)
                    "AJMAN": on_hand if stock_column == "AJMAN" else float(existing.get("AJMAN", 0) or 0),
                    "NAH": on_hand if stock_column == "NAH" else float(existing.get("NAH", 0) or 0),
                    "DEIRA": on_hand if stock_column == "DEIRA" else float(existing.get("DEIRA", 0) or 0),
                    "DEIRA2": on_hand if stock_column == "DEIRA2" else float(existing.get("DEIRA2", 0) or 0),
                    "ABUDHABI": on_hand if stock_column == "ABUDHABI" else float(existing.get("ABUDHABI", 0) or 0),
                    "QUSAIS": on_hand if stock_column == "QUSAIS" else float(existing.get("QUSAIS", 0) or 0),
                }
            else:
                # RASALKHORE branch
                # Check for cost price override
                if item_code in cost_price_overrides:
                    ras_cost_price = round(cost_price_overrides[item_code], 2)
                else:
                    ras_cost_price = round(avg_price, 2)
                
                row_data = {
                    "ItemCode": item_code,
                    "Upc Code": upc_code,
                    "Description": description,
                    "Manufacturer Name": manufacturer,
                    "Warehouse Code": whs_code,
                    "Stock Quantity": on_hand,
                    "Free Stock": 0,
                    "Selling Price": round(selling_price, 2),  # Brand margin or admin override (rounded to 2 decimals)
                    "CostPrice": ras_cost_price,  # Use override if exists, else API AvgPrice (rounded to 2 decimals)
                }
            
            items_to_insert.append(row_data)
        
        # Update database
        if items_to_insert:
            import pandas as pd
            df = pd.DataFrame(items_to_insert)
            df.to_sql("stock_items", conn, if_exists="replace", index=False)
        
        conn.commit()
        conn.close()
        
        result = {
            "success": True,
            "items_updated": len(items_to_insert),
            "warehouse_code": warehouse_code,
            "branch": branch
        }
        
        # Broadcast SSE update to all connected clients for this branch
        try:
            broadcast_sse_update(branch, {
                "type": "sync_complete",
                "warehouse_code": warehouse_code,
                "branch": branch,
                "items_updated": len(items_to_insert),
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            # Don't fail the sync if SSE broadcast fails
            print(f"SSE broadcast error: {e}")
        
        return jsonify(result)
        
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
                    COALESCE(ro.SellingPriceOverride, si."Selling Price") AS "Selling Price",
                    si."CostPrice"
                FROM stock_items si
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

        # 1. Ensure table exists before querying
        ensure_retail_override_table(dip_db)

        conn = sqlite3.connect(dip_db)
        cur = conn.cursor()

        cur.execute(f"ATTACH DATABASE '{ras_db_path}' AS ras")

        # 2. UPDATED SQL: Added TRIM() in the LEFT JOIN condition
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
              
              -- Priority: 1. AllStores Override, 2. Original Selling Price, 3. Default 0
              COALESCE(ro.SellingPriceOverride, si."Selling Price", 0) AS MinPrice,
              
              COALESCE(si."CostPrice", 0) AS CostPrice,
              CASE
                WHEN LOWER(si."Manufacturer Name") LIKE 'ariston%'
                THEN COALESCE(si."CostPrice", 0)
                ELSE (COALESCE(si."CostPrice", 0) * 1.03)
              END AS "CostPrice 2"
            FROM stock_items si
            LEFT JOIN ras.stock_items rsi ON rsi."ItemCode" = si."ItemCode"
            
            -- FIX: Join using TRIM to avoid whitespace mismatch
            LEFT JOIN retail_overrides ro
              ON TRIM(ro.ItemCode) = TRIM(si."ItemCode") 
              AND ro.Branch = 'ALLSTORES'
            
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

    return render_template(
        "stock.html",
        results=results,
        query=query,
        hide_zero_stock=hide_zero_stock,
        hide_zero_cost=False,
        branch="ALLSTORES"
    )

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
            # Update default margin
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
    
    # Get default margin
    cur.execute("SELECT margin_percent FROM brand_margins WHERE brand_name = '__DEFAULT__'")
    row = cur.fetchone()
    default_margin = row[0] if row else 15.0
    
    # Get all unique manufacturers from stock_items
    cur.execute('SELECT DISTINCT "Manufacturer Name" FROM stock_items WHERE "Manufacturer Name" IS NOT NULL AND "Manufacturer Name" != "" ORDER BY "Manufacturer Name"')
    all_manufacturers = [row[0] for row in cur.fetchall()]
    
    # Get all brand margins (excluding default)
    cur.execute("SELECT brand_name, margin_percent, edited_by, edited_at FROM brand_margins WHERE brand_name != '__DEFAULT__' ORDER BY brand_name")
    brand_margins = cur.fetchall()
    brand_margins_dict = {row[0]: {"margin": row[1], "edited_by": row[2], "edited_at": row[3]} for row in brand_margins}
    
    # Build list of all brands with their margins
    brands_list = []
    for mfg in all_manufacturers:
        if mfg in brand_margins_dict:
            brands_list.append({
                "name": mfg,
                "margin": brand_margins_dict[mfg]["margin"],
                "is_custom": True,
                "edited_by": brand_margins_dict[mfg]["edited_by"],
                "edited_at": brand_margins_dict[mfg]["edited_at"]
            })
        else:
            brands_list.append({
                "name": mfg,
                "margin": default_margin,
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
                         search_query=search_query,
                         message=message,
                         message_type=message_type,
                         total_brands=len(all_manufacturers))


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
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO brand_margins (brand_name, margin_percent, edited_by)
        VALUES (?, ?, ?)
        ON CONFLICT(brand_name) DO UPDATE SET
            margin_percent = excluded.margin_percent,
            edited_by = excluded.edited_by,
            edited_at = datetime('now')
    """, (brand_name, margin, session.get("username", "admin")))
    
    conn.commit()
    conn.close()
    
    return jsonify(ok=True, brand_name=brand_name, margin_percent=margin)


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
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT ItemCode, CostPrice FROM cost_price_overrides")
    overrides = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return overrides


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