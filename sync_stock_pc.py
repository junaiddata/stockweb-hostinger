#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PC-based sync script to fetch stock from local API and push to VPS.
This script runs on your local PC and can access http://192.168.1.103/IntegrationApi/api/Stock

WORKFLOW:
1. PC script fetches data from local API (192.168.1.103)
2. PC script sends data to VPS via HTTP API endpoint
3. VPS updates its databases
"""

import sys
import os
import requests
import json
import traceback
import time
import schedule
from datetime import datetime
from typing import Dict, List, Optional

# Log file path (in same directory as script)
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_stock.log")

def log_message(message: str, also_print: bool = True):
    """Write message to log file and optionally print to console."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {message}\n"
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception as e:
        # If logging fails, at least try to print
        print(f"Logging error: {e}")
    
    if also_print:
        print(message, flush=True)

# Configuration - EDIT THESE
API_BASE_URL = "http://192.168.1.103/IntegrationApi/api/Stock"
VPS_BASE_URL = "https://stock.junaidworld.com"  # For testing: localhost. For production: https://your-vps-domain.com
VPS_API_KEY = "rLEkUZQiljwQWPS5ZJ8m6zawpsr9QUvRqYka-hj7fBw"  # For testing. Must match app.py. Change to secure random key for production

# Warehouse mapping
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

def _to_float(x, default=0.0):
    """Convert value to float, return default if conversion fails."""
    try:
        return float(x)
    except (ValueError, TypeError):
        return default

def fetch_warehouse_data(warehouse_code: str) -> Optional[Dict]:
    """
    Fetch stock data from local API for a specific warehouse.
    
    Returns:
        dict with "Data" key containing items, or None on error
    """
    try:
        payload = {"Warehouse": warehouse_code, "Active": "Y"}
        response = requests.post(API_BASE_URL, json=payload, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        if not data or "Data" not in data:
            log_message(f"  Warning: Invalid API response for warehouse {warehouse_code}")
            return None
        
        return data
    except requests.RequestException as e:
        print(f"  Error fetching warehouse {warehouse_code}: {e}")
        return None

def transform_item_for_vps(item: Dict) -> Dict:
    """
    Transform API item format to VPS format.
    Note: We ignore API's "Sales Price" field and calculate 15% margin from AvgPrice.
    """
    item_code = str(item.get("ItemCode", "")).strip()
    if not item_code:
        return None
    
    avg_price = _to_float(item.get("AvgPrice", 0), 0.0)
    
    # Always calculate 15% margin from AvgPrice using division method
    # 15% margin = Cost / 0.85 (ignore API's "Sales Price" field)
    # Admin edits will be preserved on VPS side
    # Round to 2 decimal places
    calculated_selling_price = round(avg_price / 0.85, 2) if avg_price > 0 else 0.0
    
    return {
        "ItemCode": item_code,
        "U_UPCCODE": str(item.get("U_UPCCODE", "")).strip(),
        "ItemName": str(item.get("ItemName", "")).strip(),
        "FirmName": str(item.get("FirmName", "")).strip(),
        "WhsCode": str(item.get("WhsCode", "")).strip(),
        "OnHand": _to_float(item.get("OnHand", 0), 0.0),
        "AvgPrice": avg_price,  # This is the cost price
        "Value": _to_float(item.get("Value", 0), 0.0),
        # Calculate 15% margin for selling price (ignore API's "Sales Price")
        "Sales_Price": calculated_selling_price
    }

def sync_warehouse_to_vps(warehouse_code: str, keep_admin_prices: bool = True) -> Dict:
    """
    Fetch data from local API and send to VPS.
    
    Returns:
        dict with success, items_updated, error keys
    """
    log_message(f"\nProcessing warehouse {warehouse_code}...")
    
    # Step 1: Fetch from local API
    api_data = fetch_warehouse_data(warehouse_code)
    if not api_data:
        error_msg = "Failed to fetch from local API"
        log_message(f"  {error_msg}")
        return {"success": False, "items_updated": 0, "error": error_msg}
    
    items = api_data.get("Data", [])
    if not items:
        log_message(f"  No items returned from API")
        return {"success": True, "items_updated": 0, "error": None}
    
    # Step 2: Transform items
    transformed_items = []
    for item in items:
        transformed = transform_item_for_vps(item)
        if transformed:
            transformed_items.append(transformed)
    
    if not transformed_items:
        error_msg = "No valid items to sync"
        log_message(f"  {error_msg}")
        return {"success": True, "items_updated": 0, "error": error_msg}
    
    # Step 3: Send to VPS
    log_message(f"  Sending {len(transformed_items)} items to VPS...")
    try:
        vps_url = f"{VPS_BASE_URL}/api/sync-stock"
        payload = {
            "warehouse_code": warehouse_code,
            "items": transformed_items,
            "keep_admin_prices": keep_admin_prices,
            "api_key": VPS_API_KEY  # Security: VPS will verify this
        }
        
        response = requests.post(vps_url, json=payload, timeout=300)  # 5 min timeout
        response.raise_for_status()
        
        result = response.json()
        success = result.get("success", False)
        items_count = result.get("items_updated", len(transformed_items))
        error = result.get("error")
        
        if success:
            log_message(f"  ✓ Successfully synced {items_count} items")
        else:
            log_message(f"  ✗ Failed: {error}")
        
        return {
            "success": success,
            "items_updated": items_count,
            "error": error
        }
        
    except requests.RequestException as e:
        error_msg = f"Failed to send to VPS: {str(e)}"
        log_message(f"  ✗ {error_msg}")
        return {
            "success": False,
            "items_updated": 0,
            "error": error_msg
        }

def sync_all_warehouses_to_vps(keep_admin_prices: bool = True) -> Dict[str, Dict]:
    """Sync all warehouses from local API to VPS."""
    results = {}
    for warehouse_code in sorted(WAREHOUSE_MAPPING.keys()):
        results[warehouse_code] = sync_warehouse_to_vps(warehouse_code, keep_admin_prices)
    return results

def notify_flask_updates(results: Dict[str, Dict]):
    """
    Notify Flask that sync completed so it can broadcast SSE updates.
    This is called after all warehouses are synced.
    """
    try:
        # Group results by branch
        branch_updates = {}
        for warehouse_code, result in results.items():
            if result.get("success"):
                mapping = WAREHOUSE_MAPPING[warehouse_code]
                branch = mapping["branch"]
                items_updated = result.get("items_updated", 0)
                
                if branch not in branch_updates:
                    branch_updates[branch] = {
                        "warehouses": [],
                        "total_items": 0
                    }
                
                branch_updates[branch]["warehouses"].append({
                    "warehouse_code": warehouse_code,
                    "items_updated": items_updated
                })
                branch_updates[branch]["total_items"] += items_updated
        
        # Notify Flask for each branch
        for branch, update_data in branch_updates.items():
            # Send notification for each warehouse in the branch
            for wh_data in update_data["warehouses"]:
                try:
                    notify_url = f"{VPS_BASE_URL}/api/notify-sync-complete"
                    payload = {
                        "branch": branch,
                        "warehouse_code": wh_data["warehouse_code"],
                        "items_updated": wh_data["items_updated"]
                    }
                    
                    response = requests.post(notify_url, json=payload, timeout=5)
                    if response.status_code == 200:
                        log_message(f"  ✓ Notified Flask about {branch} warehouse {wh_data['warehouse_code']} update")
                    else:
                        log_message(f"  ⚠ Flask notification failed: {response.status_code}")
                except requests.RequestException as e:
                    # Don't fail sync if notification fails
                    log_message(f"  ⚠ Could not notify Flask: {e}")
    except Exception as e:
        # Don't fail sync if notification fails
        log_message(f"  ⚠ Error notifying Flask: {e}")

def check_vps_connection() -> bool:
    """Check if VPS/localhost is reachable before syncing."""
    try:
        # Try to connect to the health endpoint or root
        test_url = f"{VPS_BASE_URL}/"
        response = requests.get(test_url, timeout=5)
        return response.status_code in [200, 302, 404]  # Any response means server is up
    except requests.RequestException:
        return False

def main():
    """Main function to sync all warehouses from local API to VPS."""
    print("=" * 70)
    print(f"PC Stock Sync Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"Local API: {API_BASE_URL}")
    print(f"VPS URL: {VPS_BASE_URL}")
    print("-" * 70)
    
    # Check if VPS/localhost is reachable
    if not check_vps_connection():
        log_message(f"\n❌ ERROR: Cannot connect to {VPS_BASE_URL}")
        if "localhost" in VPS_BASE_URL or "127.0.0.1" in VPS_BASE_URL:
            log_message("\n💡 Flask server is not running!")
            log_message("   Start Flask with: python app.py")
            log_message("   Or: flask run --host=0.0.0.0 --port=5000")
        else:
            log_message(f"\n💡 VPS server at {VPS_BASE_URL} is not reachable")
            log_message("   Check if the server is running and accessible")
        return 1
    
    log_message(f"✓ Connected to {VPS_BASE_URL}")
    
    # Check configuration
    if VPS_API_KEY == "your-secret-api-key":
        log_message("\nERROR: Please configure VPS_API_KEY in this script!")
        log_message("Edit sync_stock_pc.py and set VPS_API_KEY to match app.py")
        return 1
    
    # Warn if using default VPS URL (but allow localhost for testing)
    if VPS_BASE_URL == "https://your-vps-domain.com":
        log_message("\nWARNING: VPS_BASE_URL not configured!")
        log_message("For testing: Use http://localhost:5000 (make sure app.py is running)")
        log_message("For production: Use your actual VPS URL")
        log_message("\nContinuing with current configuration...")
    
    keep_admin_prices = True
    
    try:
        results = sync_all_warehouses_to_vps(keep_admin_prices=keep_admin_prices)
        
        # Print summary
        log_message("\n" + "=" * 70, also_print=False)
        log_message("Sync Summary:")
        log_message("-" * 70, also_print=False)
        
        success_count = 0
        total_items = 0
        failed_warehouses = []
        
        for warehouse_code in sorted(results.keys()):
            result = results[warehouse_code]
            mapping = WAREHOUSE_MAPPING[warehouse_code]
            branch = mapping["branch"]
            column = mapping["column"]
            
            status = "✓ SUCCESS" if result["success"] else "✗ FAILED"
            items = result["items_updated"]
            error = result.get("error")
            
            log_message(f"Warehouse {warehouse_code} ({branch}/{column}): {status} - {items} items")
            
            if result["success"]:
                success_count += 1
                total_items += items
            else:
                failed_warehouses.append((warehouse_code, error))
                if error:
                    log_message(f"  Error: {error}")
        
        log_message("-" * 70, also_print=False)
        log_message(f"\nTotal: {success_count}/8 warehouses synced successfully")
        log_message(f"Total items updated: {total_items}")
        
        if failed_warehouses:
            log_message(f"\nFailed warehouses: {len(failed_warehouses)}")
            for wh, err in failed_warehouses:
                log_message(f"  - Warehouse {wh}: {err}")
        
        log_message("=" * 70, also_print=False)
        log_message(f"PC Stock Sync Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log_message("=" * 70, also_print=False)
        
        # Notify Flask to broadcast SSE updates for successful warehouses
        notify_flask_updates(results)
        
        # Exit with error code if any warehouse failed
        if failed_warehouses:
            return 1
        return 0
        
    except Exception as e:
        error_msg = f"\nFATAL ERROR: {e}"
        log_message(error_msg)
        log_message("\nTraceback:")
        tb = traceback.format_exc()
        log_message(tb)
        log_message("=" * 70, also_print=False)
        return 1

def run_scheduled_sync():
    """Wrapper function for scheduled execution."""
    try:
        main()
    except Exception as e:
        log_message(f"Error in scheduled sync: {e}")
        log_message(traceback.format_exc())

if __name__ == "__main__":
    # Parse command-line arguments
    use_local = "--local" in sys.argv
    run_once = "--once" in sys.argv
    
    # If --local flag is set, use localhost instead of VPS
    if use_local:
        original_vps_url = VPS_BASE_URL
        VPS_BASE_URL = "http://localhost:5000"
        log_message(f"🔧 LOCAL MODE: Using {VPS_BASE_URL} instead of {original_vps_url}")
    
    # Check if running as background service (no arguments) or one-time run (with --once)
    if run_once:
        # One-time run mode (for testing or manual execution)
        exit_code = main()
        sys.exit(exit_code)
    else:
        # Background service mode - runs continuously
        log_message("=" * 70)
        log_message("PC Stock Sync Service Started")
        if use_local:
            log_message("🔧 Running in LOCAL MODE (localhost)")
        log_message("=" * 70)
        log_message(f"Service will sync every 5 minutes")
        log_message(f"Log file: {LOG_FILE}")
        log_message("Press Ctrl+C to stop the service")
        log_message("=" * 70)
        
        # Schedule sync to run every 5 minutes
        schedule.every(5).minutes.do(run_scheduled_sync)
        
        # Run immediately on startup (don't wait 5 minutes)
        log_message("\nRunning initial sync...")
        run_scheduled_sync()
        
        # Keep running and checking schedule
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            log_message("\n" + "=" * 70)
            log_message("Service stopped by user")
            log_message("=" * 70)
            sys.exit(0)
