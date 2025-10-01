from flask import Flask, request, render_template, redirect, url_for
import sqlite3
import pandas as pd
import os
from flask import Flask, request, render_template, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

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
    "RASALKHORE": "stock_data_rasalkhor.db"
}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
        if "file" not in request.files:
            return "No file part", 400
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400
        if file and allowed_file(file.filename):
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], "stock_details.xlsx")
            file.save(filepath)
            process_excel(filepath)
            return render_template("home.html")
    return render_template("upload.html")

def process_excel(filepath):
    """Read sheets OUTPUT_DIP and OUTPUT_RASALKHORE from Excel and update the database."""
    xls = pd.ExcelFile(filepath)

    for branch, sheet_name in {"DIP": "OUTPUT_DIP", "RASALKHORE": "OUTPUT_RAS"}.items():
        if sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name, dtype={'Item No.': str})

            # ✅ Rename columns to standard names
            column_mapping = {
                "Item No.": "ItemCode",
                "Item Description": "Description",
                "Upc Code": "Upc Code",
                "Manufacturer Name": "Manufacturer Name",
                "Warehouse Code": "Warehouse Code",
                "In Stock": "Stock Quantity",
                "FREE STOCK": "Free Stock",
                "Minimum Selling Price": "Selling Price",
                "Minimum Selling Price": "Selling Price",
                "Cost Price": "CostPrice"  # <-- add this line
            }
            df.rename(columns=column_mapping, inplace=True)
            # ✅ Read Excel ensuring ItemCode is a string
#           df = pd.read_excel(excel_file, dtype={'ItemCode': str})

#     # ✅ Check if ItemCode has NaN values
            print(f"Before filling NaN - Data from Excel for {branch}:")
            print(df[['ItemCode', 'Upc Code', 'Description']].head(10))

#     # ✅ Handle missing values
            df['ItemCode'] = df['ItemCode'].fillna('').astype(str)
            df['Upc Code'] = df['Upc Code'].fillna('').astype(str)
            df['Selling Price'] = df['Selling Price'].fillna('').astype(str)
            df['CostPrice'] = df['CostPrice'].fillna(0).astype(str)

#     # ✅ Strip column names
            df.columns = df.columns.str.strip()
            # Fix missing values
            df.fillna(0, inplace=True)
            # print("Max Stock Quantity:", df["In Stock"].max())
            # print("Max Free Stock:", df["FREE STOCK"].max())
            # print("Max Selling Price:", df["Selling Price"].max())
            # Strip column names
            df.columns = df.columns.str.strip()

            # Ensure correct column names (adjust according to your actual data)
            expected_columns = [
                "ItemCode", "Upc Code", "Description", "Manufacturer Name",
                "Warehouse Code", "Stock Quantity", "Free Stock", "Selling Price", "CostPrice"
            ]
            df = df[expected_columns]

            update_database(branch, df)

def update_database(branch, df):
    """Replace stock data in the corresponding database."""
    db_path = DB_PATHS[branch]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS stock_items")  # Remove old data

    cursor.execute('''
        CREATE TABLE stock_items (
            "ItemCode" TEXT,
            "Upc Code" TEXT,
            "Description" TEXT,
            "Manufacturer Name" TEXT,
            "Warehouse Code" TEXT,
            "Stock Quantity" INTEGER,
            "Free Stock" INTEGER,
            "Selling Price" INTEGER
            "CostPrice" INTEGER       
        )
    ''')

    df.to_sql("stock_items", conn, if_exists="replace", index=False)

    conn.commit()
    conn.close()
    print(f"Database for {branch} updated successfully.")

@app.route("/")
def home():
    return render_template("home.html")

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

def stock_page(branch):
    results = None
    query = ""
    hide_zero_stock = False

    if request.method == "POST":
        query = request.form.get("query", "").strip().lower()
        hide_zero_stock = request.form.get("hideZeroStock") == "on"

        if query:
            conn = sqlite3.connect(DB_PATHS[branch])
            cursor = conn.cursor()

            # Split the query into individual words
            query_words = query.split()

            # Start building the SQL query
            sql_query = """
                SELECT * FROM stock_items
                WHERE
            """

            # Create conditions for each word
            conditions = []
            params = []

            for word in query_words:
                word_like = f"%{word}%"
                conditions.append(
                    """(
                        LOWER("ItemCode") LIKE ? OR
                        LOWER("Upc Code") LIKE ? OR
                        LOWER("Description") LIKE ? OR
                        LOWER("Manufacturer Name") LIKE ?
                    )"""
                )
                params.extend([word_like, word_like, word_like, word_like])

            # Join conditions with AND to ensure all words must be matched
            sql_query += " AND ".join(conditions)

            # Add condition to hide zero-stock items if checked
            if hide_zero_stock:
                sql_query += " AND \"Stock Quantity\" > 0"

            # Execute the query
            cursor.execute(sql_query, params)
            results = cursor.fetchall()

            # print(f"Search results for {branch}:", results)

            conn.close()

    return render_template("stock.html", results=results, query=query, hide_zero_stock=hide_zero_stock, branch=branch)

# Item detail page
@app.route("/item/<branch>/<item_code>")
def item_detail(branch, item_code):
    conn = sqlite3.connect(DB_PATHS[branch])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_items WHERE ItemCode = ?", (item_code,))
    item = cursor.fetchone()
    conn.close()

    if item:
        # Build a dictionary for clarity
        item_data = {
            "ItemCode": item[0],
            "UpcCode": item[1],
            "Description": item[2],
            "ManufacturerName": item[3],
            "WarehouseCode": item[4],
            "StockQuantity": item[5],
            "FreeStock": item[6],
            "MinSellingPrice": item[7],
            # "CostPrice": item[8]    # <-- add this

        }
                # Only show CostPrice if logged in
        if "username" in session:
            item_data["CostPrice"] = item[8]
        else:
            item_data["CostPrice"] = None  # Or leave out entirely
        print(f"Item fetched for {branch}:", item_data)
        return render_template("item_detail.html", item=item_data, branch=branch)
    else:
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
    # If you want, you can still accept warehouse param but ignore it here
    warehouse = request.args.get('warehouse')  # not used

    # Choose which DB you want to read, e.g. "DIP"
    db_path = DB_PATHS.get("DIP")
    if not db_path:
        return jsonify({"error": "Database path not found"}), 500

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT "ItemCode", "Description", "Manufacturer Name", "Warehouse Code", "Stock Quantity","Selling Price","CostPrice","Upc Code"
        FROM stock_items
    """)

    rows = cursor.fetchall()
    conn.close()

    stock_list = [
        {
            "item_code": row[0],
            "description": row[1],
            "manufacturer": row[2],
            "warehouse": row[3],
            "stock_quantity": row[4],
            "minimum_selling_price": row[5],
            "cost_price": row[6],
            "upc_code": row[7]
        }
        for row in rows
    ]

    return jsonify(stock_list)
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)


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
