#!/usr/bin/env python3
"""
Safe duplicate finder & remover for VERA SOLAR brand.
Run on VPS:  python3 fix_vera_duplicates.py

No database locks, no table drops, no data loss.
Shows everything before asking for confirmation.
"""

import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILES = {
    "DIP": os.path.join(BASE_DIR, "stock_data_headoffice.db"),
    "RASALKHORE": os.path.join(BASE_DIR, "stock_data_rasalkhor.db"),
}


def check_db(db_path, db_name):
    """Check a single DB for all types of duplicates that could cause repeated rows."""
    if not os.path.exists(db_path):
        print(f"  [{db_name}] DB not found, skipping.")
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    actions = []

    # ---- 1. Duplicate ItemCodes in stock_items ----
    cur.execute("""
        SELECT "ItemCode", COUNT(*) as cnt FROM stock_items
        GROUP BY "ItemCode" HAVING cnt > 1
    """)
    dup_items = cur.fetchall()
    if dup_items:
        print(f"\n  [{db_name}] DUPLICATE ItemCodes in stock_items:")
        for row in dup_items:
            code = row["ItemCode"]
            cnt = row["cnt"]
            cur.execute("""
                SELECT rowid, "ItemCode", "Description", "Manufacturer Name",
                       "Stock Quantity", "Selling Price"
                FROM stock_items WHERE "ItemCode" = ? ORDER BY rowid
            """, (code,))
            dupes = cur.fetchall()
            print(f"    ItemCode={code}  appears {cnt} times:")
            for d in dupes:
                print(f"      rowid={d['rowid']:>6}  Desc={d['Description'][:45]:<45}  "
                      f"Mfg={d['Manufacturer Name']:<20}  Qty={d['Stock Quantity']}  Price={d['Selling Price']}")
            actions.append(("stock_items_dup_itemcode", code, [d["rowid"] for d in dupes]))

    # ---- 2. Duplicate brand_name in brand_margins (causes JOIN to multiply rows!) ----
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='brand_margins'")
    if cur.fetchone():
        cur.execute("""
            SELECT LOWER(TRIM(brand_name)) as norm, COUNT(*) as cnt
            FROM brand_margins
            GROUP BY norm HAVING cnt > 1
        """)
        dup_brands = cur.fetchall()
        if dup_brands:
            print(f"\n  [{db_name}] DUPLICATE brand_names in brand_margins (THIS causes search duplicates!):")
            for row in dup_brands:
                norm = row["norm"]
                cnt = row["cnt"]
                cur.execute("""
                    SELECT rowid, brand_name, margin_percent, use_admin_price, edited_by
                    FROM brand_margins
                    WHERE LOWER(TRIM(brand_name)) = ? ORDER BY rowid
                """, (norm,))
                dupes = cur.fetchall()
                print(f"    brand '{norm}' appears {cnt} times:")
                for d in dupes:
                    print(f"      rowid={d['rowid']:>6}  brand_name='{d['brand_name']}'  "
                          f"margin={d['margin_percent']}%  use_admin={d['use_admin_price']}  by={d['edited_by']}")
                actions.append(("brand_margins_dup", norm, [d["rowid"] for d in dupes]))

    # ---- 3. Duplicate ItemCode in price_overrides ----
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='price_overrides'")
    if cur.fetchone():
        cur.execute("""
            SELECT ItemCode, COUNT(*) as cnt FROM price_overrides
            GROUP BY ItemCode HAVING cnt > 1
        """)
        dup_po = cur.fetchall()
        if dup_po:
            print(f"\n  [{db_name}] DUPLICATE ItemCodes in price_overrides:")
            for row in dup_po:
                code = row["ItemCode"]
                cnt = row["cnt"]
                cur.execute("""
                    SELECT rowid, ItemCode, SellingPriceOverride, edited_by
                    FROM price_overrides WHERE ItemCode = ? ORDER BY rowid
                """, (code,))
                dupes = cur.fetchall()
                print(f"    ItemCode={code}  appears {cnt} times:")
                for d in dupes:
                    print(f"      rowid={d['rowid']:>6}  Price={d['SellingPriceOverride']}  by={d['edited_by']}")
                actions.append(("price_overrides_dup", code, [d["rowid"] for d in dupes]))

    # ---- 4. Show all VERA items for visual check ----
    cur.execute("""
        SELECT rowid, "ItemCode", "Description", "Manufacturer Name",
               "Stock Quantity", "Selling Price", "CostPrice"
        FROM stock_items
        WHERE UPPER(TRIM("Manufacturer Name")) LIKE '%VERA%'
        ORDER BY "Description", rowid
    """)
    vera_items = cur.fetchall()
    if vera_items:
        print(f"\n  [{db_name}] All VERA items ({len(vera_items)} rows):")
        for item in vera_items:
            print(f"    rowid={item['rowid']:>6}  Code={item['ItemCode']:<12}  "
                  f"Desc={item['Description'][:45]:<45}  Qty={item['Stock Quantity']}  Price={item['Selling Price']}")

    conn.close()
    return actions


def fix_duplicates(db_path, db_name, actions):
    """Remove duplicates, keeping the row with the HIGHEST rowid (most recent)."""
    if not actions:
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    total_removed = 0

    for action_type, key, rowids in actions:
        keep_rowid = max(rowids)
        delete_rowids = [r for r in rowids if r != keep_rowid]

        if action_type == "stock_items_dup_itemcode":
            for rid in delete_rowids:
                cur.execute("DELETE FROM stock_items WHERE rowid = ?", (rid,))
                total_removed += cur.rowcount
                print(f"    Removed stock_items rowid={rid} (kept rowid={keep_rowid}) for ItemCode={key}")

        elif action_type == "brand_margins_dup":
            for rid in delete_rowids:
                cur.execute("DELETE FROM brand_margins WHERE rowid = ?", (rid,))
                total_removed += cur.rowcount
                print(f"    Removed brand_margins rowid={rid} (kept rowid={keep_rowid}) for brand='{key}'")

        elif action_type == "price_overrides_dup":
            for rid in delete_rowids:
                cur.execute("DELETE FROM price_overrides WHERE rowid = ?", (rid,))
                total_removed += cur.rowcount
                print(f"    Removed price_overrides rowid={rid} (kept rowid={keep_rowid}) for ItemCode={key}")

    conn.commit()
    conn.close()
    print(f"  [{db_name}] Total rows removed: {total_removed}")


def main():
    print("=" * 70)
    print("  VERA Duplicate Checker & Cleaner")
    print("=" * 70)

    all_actions = {}

    # Phase 1: Diagnose
    print("\n--- PHASE 1: CHECKING ALL TABLES FOR DUPLICATES ---")
    for db_name, db_path in DB_FILES.items():
        print(f"\n{'─' * 50}")
        print(f"Checking {db_name} ({os.path.basename(db_path)})...")
        actions = check_db(db_path, db_name)
        all_actions[db_name] = actions

    total = sum(len(a) for a in all_actions.values())
    if total == 0:
        print("\n\nNo duplicates found in any table. Database is clean.")
        sys.exit(0)

    # Phase 2: Fix
    total_to_remove = sum(len(rowids) - 1 for actions in all_actions.values() for _, _, rowids in actions)
    print(f"\n--- PHASE 2: REMOVAL ({total_to_remove} duplicate row(s) to remove) ---\n")
    answer = input("Remove the duplicates listed above? (yes/no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("Aborted. No changes made.")
        sys.exit(0)

    print()
    for db_name, db_path in DB_FILES.items():
        actions = all_actions[db_name]
        if actions:
            print(f"Fixing {db_name}...")
            fix_duplicates(db_path, db_name, actions)
        else:
            print(f"  [{db_name}] Clean, nothing to fix.")

    print("\nDone! Refresh the web app to verify.")


if __name__ == "__main__":
    main()
