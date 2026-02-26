#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VPS sync CLI - runs stock sync from API (via SSH tunnel at localhost:8443).
Use with cron: python manage.py sync_all

Replaces sync_stock_pc.py - no PC sync needed. PC only runs SSH tunnel.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Load .env before importing app
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Setup logging before imports that may log
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "sync_vps.log")

def _setup_logging():
    """Configure rotating file handler (10MB, 5 backups)."""
    logger = logging.getLogger("sync_vps")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    logger.addHandler(handler)
    return logger

logger = _setup_logging()

def log(msg, also_print=True):
    logger.info(msg)
    if also_print:
        print(msg, flush=True)

def sync_all():
    """Run sync for all warehouses (same logic as sync_stock_pc.py)."""
    # Import here so .env is loaded first
    from app import app, sync_all_warehouses_from_api, broadcast_sse_update, WAREHOUSE_MAPPING, API_BASE_URL

    log(f"VPS Sync Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"API: {API_BASE_URL}")

    with app.app_context():
        results = sync_all_warehouses_from_api(keep_admin_prices=True)

    success_count = 0
    total_items = 0
    failed = []

    for warehouse_code in sorted(results.keys()):
        r = results[warehouse_code]
        mapping = WAREHOUSE_MAPPING[warehouse_code]
        branch = mapping["branch"]
        column = mapping["column"]
        status = "OK" if r["success"] else "FAILED"
        items = r["items_updated"]
        error = r.get("error")

        log(f"Warehouse {warehouse_code} ({branch}/{column}): {status} - {items} items")
        if error:
            log(f"  Error: {error}")
            failed.append((warehouse_code, error))
        else:
            success_count += 1
            total_items += items
            # Broadcast SSE so stock pages refresh
            try:
                broadcast_sse_update(branch, {
                    "type": "sync_complete",
                    "warehouse_code": warehouse_code,
                    "branch": branch,
                    "items_updated": items,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception as e:
                log(f"  SSE broadcast failed: {e}")

    log(f"Total: {success_count}/8 warehouses, {total_items} items")
    log(f"VPS Sync Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return 0 if not failed else 1

def main():
    if len(sys.argv) < 2 or sys.argv[1] != "sync_all":
        print("Usage: python manage.py sync_all")
        print("Runs stock sync from API (via tunnel at localhost:8443). Use in cron.")
        sys.exit(1)

    try:
        exit_code = sync_all()
        sys.exit(exit_code)
    except Exception as e:
        log(f"FATAL: {e}", also_print=True)
        import traceback
        log(traceback.format_exc(), also_print=False)
        sys.exit(1)

if __name__ == "__main__":
    main()
