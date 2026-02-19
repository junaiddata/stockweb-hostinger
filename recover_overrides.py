#!/usr/bin/env python3
"""
Try to extract price_overrides, brand_margins, retail_overrides from a corrupted SQLite DB.
Run on VPS: python3 recover_overrides.py stock_data_headoffice.db.corrupted
"""
import sqlite3
import sys
import os

def try_dump_table(conn, table, out_file):
    """Try to read all rows from a table and write to SQL file."""
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        col_names = [d[0] for d in cur.description]
        if not rows:
            print(f"  {table}: empty")
            return 0
        # Write as INSERT statements
        with open(out_file, "w") as f:
            for row in rows:
                vals = ", ".join(repr(v) for v in row)
                cols = ", ".join(f'"{c}"' for c in col_names)
                f.write(f'INSERT OR REPLACE INTO {table} ({cols}) VALUES ({vals});\n')
        print(f"  {table}: {len(rows)} rows -> {out_file}")
        return len(rows)
    except Exception as e:
        print(f"  {table}: FAILED - {e}")
        return 0

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 recover_overrides.py <corrupted_db_path>")
        print("Example: python3 recover_overrides.py stock_data_headoffice.db.corrupted")
        sys.exit(1)
    db_path = sys.argv[1]
    if not os.path.isfile(db_path):
        print(f"File not found: {db_path}")
        sys.exit(1)
    out_dir = os.path.dirname(os.path.abspath(db_path))
    print(f"Opening {db_path} (read-only)...")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA read_uncommitted = 1")
    except Exception as e:
        print(f"Cannot open DB: {e}")
        sys.exit(1)
    total = 0
    for table, out_name in [
        ("price_overrides", "recovered_price_overrides.sql"),
        ("brand_margins", "recovered_brand_margins.sql"),
        ("retail_overrides", "recovered_retail_overrides.sql"),
    ]:
        out_path = os.path.join(out_dir, out_name)
        n = try_dump_table(conn, table, out_path)
        total += n
    conn.close()
    print(f"\nTotal rows recovered: {total}")
    if total > 0:
        print("Import into new DB with: sqlite3 stock_data_headoffice.db < recovered_*.sql")

if __name__ == "__main__":
    main()
