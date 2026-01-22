---
name: Free Stock Calculation from Sales Orders
overview: Implement Free Stock calculation by fetching Sales Orders from PC-accessible API (last 3 months dynamically calculated), calculating Free Stock = DIP Stock - Total Open SO quantity (for HO customers), and syncing to VPS. Free Stock sync will run on a separate schedule from stock sync.
todos:
  - id: pc_sales_order_api
    content: Add Sales Order API fetch function in sync_stock_pc.py with date range support
    status: pending
  - id: pc_free_stock_calc
    content: Implement Free Stock calculation logic (filter HO customers, Open status, sum OpenQty)
    status: pending
    dependencies:
      - pc_sales_order_api
  - id: pc_free_stock_sync
    content: Add function to send Free Stock map to VPS endpoint /api/sync-free-stock
    status: pending
    dependencies:
      - pc_free_stock_calc
  - id: pc_schedule_integration
    content: Add separate schedule for Free Stock sync (every 15 minutes) in sync_stock_pc.py
    status: pending
    dependencies:
      - pc_free_stock_sync
  - id: vps_free_stock_endpoint
    content: Create /api/sync-free-stock POST endpoint in app.py with API key authentication
    status: pending
  - id: vps_update_logic
    content: Implement UPDATE query to set Free Stock column for DIP items (preserve other columns)
    status: pending
    dependencies:
      - vps_free_stock_endpoint
  - id: vps_sse_broadcast
    content: Add SSE broadcast for Free Stock updates to DIP branch clients
    status: pending
    dependencies:
      - vps_update_logic
  - id: error_handling
    content: Add comprehensive error handling and logging for both PC and VPS sides
    status: pending
    dependencies:
      - pc_schedule_integration
      - vps_sse_broadcast
---

# Free Stock Calculation from Sales Orders API

## Overview

Implement Free Stock calculation by fetching Sales Orders from the PC-accessible API endpoint, calculating Free Stock for DIP items based on open sales orders from HO customers, and syncing the calculated values to VPS.

## Architecture Flow

```
PC Script (sync_stock_pc.py)
    ↓
1. Fetch Sales Orders from API (192.168.1.103/IntegrationApi/api/SalesOrderStock)
    ↓
2. Filter: LineStatus="O" AND CardCode starts with "HO"
    ↓
3. Group by ItemCode, sum OpenQty
    ↓
4. Calculate Free Stock Map: {ItemCode: DIP_Stock - Total_OpenQty}
    ↓
5. Send to VPS via new endpoint /api/sync-free-stock
    ↓
VPS (app.py)
    ↓
6. Update Free Stock column in stock_items table (DIP branch only)
    ↓
7. Broadcast SSE update to connected clients
```

## Implementation Details

### 1. PC Script Changes (`sync_stock_pc.py`)

#### Add Sales Order API Configuration

- Add `SALES_ORDER_API_URL = "http://192.168.1.103/IntegrationApi/api/SalesOrderStock"`
- Date range calculated dynamically: `to_date = today`, `from_date = today - 3 months`
- Use `datetime` and `timedelta` to calculate dates automatically

#### New Function: `get_sales_order_date_range()`

- Calculate `to_date` as today's date (format: "YYYY-MM-DD")
- Calculate `from_date` as 3 months ago from today
- Handle month boundaries correctly (e.g., Jan 15 → Oct 15)
- Return tuple: `(from_date_str, to_date_str)`

#### New Function: `fetch_sales_orders(from_date, to_date)`

- POST request to Sales Order API with dynamically calculated date range
- Date format: "YYYY-MM-DD" (e.g., "2025-10-21" to "2026-01-21")
- Returns list of sales order items
- Handle pagination if API returns large datasets (currently shows Count: 50024)
- Error handling and logging

#### New Function: `calculate_free_stock_map(sales_orders, dip_stock_map)`

- Filter sales orders:
  - `LineStatus == "O"` (Open only)
  - `CardCode.startswith("HO")` (HO customers only)
- Group by `ItemCode`, sum `OpenQty` for each item
- Calculate: `Free Stock = DIP Stock - Total OpenQty`
- Return dict: `{ItemCode: calculated_free_stock}`

#### New Function: `sync_free_stock_to_vps(free_stock_map)`

- Send free stock data to VPS endpoint `/api/sync-free-stock`
- Include API key for authentication
- Handle errors gracefully (don't fail stock sync if free stock fails)

#### New Function: `sync_free_stock()` (Main entry point)

- Calculate date range dynamically (today - 3 months to today)
- Fetch DIP stock data (to get current DIP Stock quantities)
- Fetch Sales Orders from API using calculated date range
- Calculate Free Stock map
- Send to VPS
- Log results (include date range used in logs)

#### Schedule Integration

- Add separate schedule for Free Stock sync (e.g., every 15 minutes)
- Run independently from stock sync
- Can be disabled via configuration

### 2. VPS Changes (`app.py`)

#### New Endpoint: `/api/sync-free-stock` (POST)

- Accept JSON payload:
  ```json
  {
    "api_key": "...",
    "free_stock_map": {
      "ItemCode1": 10.0,
      "ItemCode2": 5.5,
      ...
    }
  }
  ```

- Verify API key matches `VPS_API_KEY`
- Update `Free Stock` column in `stock_data_headoffice.db` (DIP branch only)
- Only update items that exist in database
- Preserve other columns (don't replace entire table)
- Broadcast SSE update to DIP branch clients
- Return success/error response

#### Update Logic

- Connect to DIP database
- For each ItemCode in free_stock_map:
  - UPDATE `stock_items` SET `Free Stock` = ? WHERE `ItemCode` = ?
- Commit transaction
- Handle errors (item not found, database errors)

### 3. Database Schema

- No schema changes needed (Free Stock column already exists)
- Ensure Free Stock column exists in DIP database (already present)

### 4. Error Handling

#### PC Script

- If Sales Order API fails: Log error, don't update Free Stock (keep existing values)
- If VPS sync fails: Log error, retry on next cycle
- If date calculation fails: Use fallback (today - 90 days to today)
- Import `datetime` and `timedelta` for date calculations

#### VPS

- If API key invalid: Return 401 Unauthorized
- If item not found: Skip (log warning), continue with other items
- If database error: Rollback transaction, return error

### 5. Logging

- Log Free Stock sync start/end times
- Log number of items updated
- Log any errors or warnings
- Add to existing `sync_stock.log` file

### 6. Configuration

#### PC Script (`sync_stock_pc.py`)

```python
# Free Stock Sync Configuration
FREE_STOCK_SYNC_ENABLED = True
FREE_STOCK_SYNC_INTERVAL_MINUTES = 15
SALES_ORDER_MONTHS_BACK = 3  # Look back 3 months from today

# Date range calculated dynamically:
# from_date = today - timedelta(days=90)  # Approximately 3 months
# to_date = today
# Format: "YYYY-MM-DD" (e.g., "2025-10-21" to "2026-01-21")
```

#### VPS (`app.py`)

- Use existing `VPS_API_KEY` for authentication
- No new configuration needed

## Files to Modify

1. **`sync_stock_pc.py`**

   - Import `datetime` and `timedelta` for date calculations
   - Add date range calculation function (today - 3 months)
   - Add Sales Order API functions
   - Add Free Stock calculation logic
   - Add separate schedule for Free Stock sync
   - Integrate with existing sync workflow

2. **`app.py`**

   - Add `/api/sync-free-stock` endpoint
   - Add Free Stock update logic (UPDATE query, not REPLACE)
   - Add SSE broadcast for Free Stock updates

## Testing Considerations

1. Verify date range calculation: Should always be today minus 3 months to today
2. Test date calculation across month boundaries (e.g., Jan 15 → Oct 15)
3. Verify Free Stock calculation: DIP Stock = 100, OpenQty = 20 → Free Stock = 80
4. Test with items that have no open orders (Free Stock = DIP Stock)
5. Test with items not in database (should skip)
6. Test API failure scenarios
7. Verify SSE updates are broadcast correctly
8. Verify date range updates automatically each day (no manual changes needed)

## Edge Cases

1. **Large dataset**: Sales Order API returns 50K+ records

   - Process in batches if needed
   - Optimize grouping/summing logic

2. **Item not in DIP database**: Skip (log warning)

3. **Negative Free Stock**: Allow negative values (indicates oversold)

4. **Multiple warehouses**: Only calculate for DIP Stock (warehouse 01)

5. **Date range calculation**: Automatically updates daily (always last 3 months from today)

## Performance

- Free Stock sync runs separately (every 15 min) to avoid blocking stock sync
- Use efficient grouping (dict/set operations)
- Batch database updates if possible
- Cache Sales Order data if API is slow (optional)

## Future Enhancements (Not in Scope)

- Real-time Free Stock updates (currently batch)
- Free Stock calculation for retail branches
- Historical Free Stock tracking
- Free Stock alerts/thresholds
