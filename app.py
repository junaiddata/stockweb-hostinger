from flask import Flask, request, render_template, redirect, url_for
import sqlite3
import pandas as pd
import os
from flask import Flask, request, render_template, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash
import requests


app = Flask(__name__)

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
                sql_query += ' AND si."Stock Quantity" > 0'
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

    # --- Retail branches read from DIP DB and use retail_overrides ---
    if branch in RETAIL_BRANCHES:
        db_path = DB_PATHS["DIP"]
        try:
            ensure_retail_override_table(db_path)  # safe no-op if already created
        except Exception:
            pass

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # NOTE: column name must be interpolated; branch is validated via RETAIL_BRANCHES.
        cur.execute(f"""
            SELECT
                si."ItemCode",
                si."Upc Code",
                si."Description",
                si."Manufacturer Name",
                si."Warehouse Code",
                COALESCE(si."{branch}", 0)               AS retail_stock,
                0                                        AS free_stock,
                COALESCE(ro.SellingPriceOverride, si."Selling Price") AS eff_min_price,
                si."CostPrice"
            FROM stock_items si
            LEFT JOIN retail_overrides ro
              ON ro.ItemCode = si."ItemCode" AND ro.Branch = ?
            WHERE si."ItemCode" = ?
        """, (branch, item_code))
        row = cur.fetchone()
        conn.close()

        if not row:
            return render_template("item_detail.html", item=None, branch=branch), 404

        item_data = {
            "ItemCode": row[0],
            "UpcCode": row[1],
            "Description": row[2],
            "ManufacturerName": row[3],
            "WarehouseCode": row[4],
            "StockQuantity": row[5],
            "FreeStock": row[6],
            "MinSellingPrice": row[7],
            "CostPrice": row[8] if "username" in session else None,
        }
        return render_template("item_detail.html", item=item_data, branch=branch)

    # --- Alabama (no stock/min price) ---
    if branch == "ALABAMA":
        db_path = DB_PATHS[branch]
        ensure_override_table(db_path)  # harmless for ALABAMA
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT "ItemCode","Upc Code","Description","Manufacturer Name","CostPrice"
            FROM stock_items
            WHERE "ItemCode" = ?
        """, (item_code,))
        item = cur.fetchone()
        conn.close()

        if item:
            item_data = {
                "ItemCode": item[0],
                "UpcCode": item[1],
                "Description": item[2],
                "ManufacturerName": item[3],
                "WarehouseCode": None,
                "StockQuantity": None,
                "FreeStock": None,
                "MinSellingPrice": None,
                "CostPrice": item[4] if "username" in session else None,
            }
            return render_template("item_detail.html", item=item_data, branch=branch)
        return render_template("item_detail.html", item=None, branch=branch), 404

    # --- DIP / RASALKHORE (existing behavior with price_overrides) ---
    db_path = DB_PATHS[branch]
    ensure_override_table(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
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
        LEFT JOIN price_overrides po ON po.ItemCode = si."ItemCode"
        WHERE si."ItemCode" = ?
    """, (item_code,))
    item = cur.fetchone()
    conn.close()

    if item:
        item_data = {
            "ItemCode": item[0],
            "UpcCode": item[1],
            "Description": item[2],
            "ManufacturerName": item[3],
            "WarehouseCode": item[4],
            "StockQuantity": item[5],
            "FreeStock": item[6],
            "MinSellingPrice": item[7],
            "CostPrice": item[8] if "username" in session else None,
        }
        return render_template("item_detail.html", item=item_data, branch=branch)

    return render_template("item_detail.html", item=None, branch=branch), 404



# (Optional) Route to update data manually
@app.route("/update_data/<branch>", methods=["GET"])
def update_data(branch):
    if branch not in DB_PATHS:
        return f"Branch '{branch}' not found.", 404

    # Force update the database for the specified branch
    initialize_db(branch, force_update=True)
    return redirect(url_for("home"))

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
    

@app.route("/allstores", methods=["GET", "POST"])
def allstores():
    """
    One row per item with retail branch columns:
    [ItemCode, Upc, Description, AJMAN, NAH, DEIRA, DEIRA2, ABUDHABI, QUSAIS,
     TotalRetail, MinPrice, Cost]
    MinPrice can have its own ALLSTORES override (separate from DIP).
    """
    results = None
    query = ""
    hide_zero_stock = False

    if request.method == "POST":
        query = (request.form.get("query") or "").strip().lower()
        hide_zero_stock = request.form.get("hideZeroStock") == "on"

        # Build WHERE across ItemCode/UPC/Description/Manufacturer (all from si)
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

        # make sure retail_overrides exists (for ALLSTORES overrides)
        ensure_retail_override_table(dip_db)

        conn = sqlite3.connect(dip_db)
        cur = conn.cursor()

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
              (
                COALESCE(si."AJMAN", 0) + COALESCE(si."NAH", 0) +
                COALESCE(si."DEIRA", 0) + COALESCE(si."DEIRA2", 0) +
                COALESCE(si."ABUDHABI", 0) + COALESCE(si."QUSAIS", 0)
              ) AS TotalRetail,
              COALESCE(ro.SellingPriceOverride, si."Selling Price", 0) AS MinPrice,
              COALESCE(si."CostPrice", 0) AS CostPrice,
              CASE
                WHEN LOWER(si."Manufacturer Name") LIKE 'ariston%'
                THEN COALESCE(si."CostPrice", 0)
                ELSE (COALESCE(si."CostPrice", 0) * 1.03)
              END AS "CostPrice 2"
            FROM stock_items si
            LEFT JOIN retail_overrides ro
              ON ro.ItemCode = si."ItemCode" AND ro.Branch = 'ALLSTORES'
            WHERE {where_sql}
            {" AND (" + " + ".join([
                'COALESCE(si."AJMAN", 0)',
                'COALESCE(si."NAH", 0)',
                'COALESCE(si."DEIRA", 0)',
                'COALESCE(si."DEIRA2", 0)',
                'COALESCE(si."ABUDHABI", 0)',
                'COALESCE(si."QUSAIS", 0)'
            ]) + ") > 0" if hide_zero_stock else ""}
            ORDER BY si."ItemCode"
        """

        cur.execute(sql, params)
        results = cur.fetchall()
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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000 , debug=True)