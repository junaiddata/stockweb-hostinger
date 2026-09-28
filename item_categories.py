"""
Brand + Pipe/Fitting categories for Hepworth, Ultra and GF items.

Categories live in their own table (`item_categories`) inside each branch DB,
NOT as a column on `stock_items`. The stock sync and the Excel upload use
INSERT OR REPLACE on stock_items, which would wipe any extra column; a separate
table is never touched by them, so categories survive cron runs and uploads.

Rows have Source 'auto' (rule based, refreshed on every run) or 'manual'
(set by an admin, never touched by the auto rules).
"""
import re
import sqlite3
from datetime import datetime

CATEGORIES = [
    "Hepworth Pipe", "Hepworth Fitting",
    "Ultra Pipe", "Ultra Fitting",
    "GF Pipe", "GF Fitting",
]

# Manufacturer Name (upper-cased) -> brand group.
# HEPWORTH, HEPWORTH-UPVC ...; ULTRA, ULTRAFLOW; GF, GF-HP, GF-UPVC ...
_BRAND_RES = [
    ("Hepworth", re.compile(r"^HEPWORTH(?:$|[\s\-_])")),
    ("Ultra", re.compile(r"^ULTRA(?:FLOW)?(?:$|[\s\-_])")),
    ("GF", re.compile(r"^GF(?:$|[\s\-_])")),
]

# An item is a Pipe when its description says so ...
_PIPE_RE = re.compile(
    r"\bPIPES?\b|DRAINPIPE|\bPR\.?PIPE\b|\bP/E\b"          # PIPE, DRAINPIPE, PR.PIPE, DACTA SOIL P/E 4M
    r"|\bDUCT\s+(?:SS|PE|RR)\b|\bCL\s*'?\s*[A-E]\s*'?\s+DUCT\b"   # DUCT SS 6", CL 'D' DUCT SS
    r"|\b(?:SOIL|DRAIN)\s+(?:KM\s+)?(?:PE|SS)\b"             # DACTA SOIL SS 200MM, DRAIN KM PE 110MM
    r"|\bKM\s+PRESSURE\s+SS\b",                              # CL D /PN12 KM PRESSURE SS 3"
    re.IGNORECASE,
)
# ... unless it is an accessory / consumable that merely mentions a pipe.
_NOT_PIPE_RE = re.compile(
    r"LUBRICANT|CEMENT|CLIP|CLAMP|BRACKET|CUTTER|SADDLE|BEND\b|ELBOW|\bTEE\b|COUPL|REDUC"
    r"|BUSH|ADAPT|UNION|\bPLUG\b|END\s*CAP|FLANGE|VALVE|GASKET|\bTOOL|SUPPORT|HANGER",
    re.IGNORECASE,
)


def brand_group(manufacturer):
    m = (manufacturer or "").strip().upper()
    for group, rx in _BRAND_RES:
        if rx.match(m):
            return group
    return None


def is_pipe(description, manufacturer=""):
    d = (description or "")
    if _NOT_PIPE_RE.search(d):
        return False
    if _PIPE_RE.search(d):
        return True
    # e.g. manufacturer "HEPWORTH-DACTA SOLVENT PIPE" holds the solvent-weld pipes
    return "PIPE" in (manufacturer or "").upper()


def classify(description, manufacturer):
    """Return e.g. 'Hepworth Pipe' / 'Ultra Fitting', or None for other brands."""
    group = brand_group(manufacturer)
    if not group:
        return None
    return f"{group} {'Pipe' if is_pipe(description, manufacturer) else 'Fitting'}"


def ensure_item_categories_table(db_path, timeout=30.0):
    """Create item_categories if missing; populate it once when first created."""
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='item_categories'")
        existed = cur.fetchone() is not None
        if not existed:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_categories (
                    ItemCode TEXT PRIMARY KEY,
                    Category TEXT NOT NULL,
                    Source TEXT NOT NULL DEFAULT 'auto',
                    updated_at TEXT
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_item_categories_cat ON item_categories(Category)")
            conn.commit()
    finally:
        conn.close()
    if not existed:
        auto_categorize(db_path, timeout=timeout)


def auto_categorize(db_path, timeout=30.0):
    """
    Refresh 'auto' rows from stock_items. 'manual' rows are never modified.
    Only reads stock_items and only writes item_categories, so it cannot affect
    stock, prices or overrides. Returns (inserted_or_updated, removed).
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_items'")
        if not cur.fetchone():
            return 0, 0
        cur.execute("""
            CREATE TABLE IF NOT EXISTS item_categories (
                ItemCode TEXT PRIMARY KEY,
                Category TEXT NOT NULL,
                Source TEXT NOT NULL DEFAULT 'auto',
                updated_at TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_item_categories_cat ON item_categories(Category)")

        cur.execute("""
            SELECT "ItemCode", "Description", "Manufacturer Name" FROM stock_items
            WHERE UPPER("Manufacturer Name") LIKE 'HEPWORTH%'
               OR UPPER("Manufacturer Name") LIKE 'ULTRA%'
               OR UPPER("Manufacturer Name") LIKE 'GF%'
        """)
        wanted = {}
        for code, desc, mfg in cur.fetchall():
            cat = classify(desc, mfg)
            if cat and code:
                wanted[str(code).strip()] = cat

        cur.execute("SELECT ItemCode, Category, Source FROM item_categories")
        existing = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        changed = 0
        for code, cat in wanted.items():
            cur_row = existing.get(code)
            if cur_row is None:
                cur.execute(
                    "INSERT INTO item_categories (ItemCode, Category, Source, updated_at) VALUES (?, ?, 'auto', ?)",
                    (code, cat, now))
                changed += 1
            elif cur_row[1] == "auto" and cur_row[0] != cat:
                cur.execute(
                    "UPDATE item_categories SET Category = ?, updated_at = ? WHERE ItemCode = ? AND Source = 'auto'",
                    (cat, now, code))
                changed += 1

        # auto rows whose item left these brands / was removed from stock_items
        stale = [c for c, (_, src) in existing.items() if src == "auto" and c not in wanted]
        for i in range(0, len(stale), 500):
            chunk = stale[i:i + 500]
            cur.execute(
                f"DELETE FROM item_categories WHERE Source = 'auto' AND ItemCode IN ({','.join('?' * len(chunk))})",
                chunk)
        conn.commit()
        return changed, len(stale)
    finally:
        conn.close()


def safe_auto_categorize(db_path):
    """Never raises: used after sync/upload so a failure here cannot break them."""
    try:
        return auto_categorize(db_path)
    except Exception as e:
        print(f"[item_categories] auto-categorize skipped for {db_path}: {e}")
        return 0, 0


def set_manual_category(db_path, item_code, category, edited_by=None):
    """category=None/'' removes the manual override (auto rule applies again on next run)."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cur = conn.cursor()
        item_code = (item_code or "").strip()
        if not category:
            cur.execute("DELETE FROM item_categories WHERE ItemCode = ? AND Source = 'manual'", (item_code,))
        else:
            cur.execute("""
                INSERT INTO item_categories (ItemCode, Category, Source, updated_at)
                VALUES (?, ?, 'manual', ?)
                ON CONFLICT(ItemCode) DO UPDATE SET Category = excluded.Category,
                    Source = 'manual', updated_at = excluded.updated_at
            """, (item_code, category, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()


def get_category_map(db_path, item_codes):
    """{ItemCode: Category} for display. Never raises; returns {} on any problem."""
    codes = sorted({str(c).strip() for c in (item_codes or []) if c is not None and str(c).strip()})
    if not codes:
        return {}
    try:
        ensure_item_categories_table(db_path)
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            out = {}
            for i in range(0, len(codes), 500):
                chunk = codes[i:i + 500]
                cur = conn.execute(
                    f"SELECT ItemCode, Category FROM item_categories WHERE ItemCode IN ({','.join('?' * len(chunk))})",
                    chunk)
                out.update(cur.fetchall())
            return out
        finally:
            conn.close()
    except Exception as e:
        print(f"[item_categories] category lookup skipped: {e}")
        return {}


def get_category_counts(db_path):
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        cur = conn.cursor()
        cur.execute("SELECT Category, Source, COUNT(*) FROM item_categories GROUP BY Category, Source")
        return cur.fetchall()
    finally:
        conn.close()
