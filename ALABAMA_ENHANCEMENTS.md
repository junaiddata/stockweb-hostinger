# Alabama Enhancements Summary

## Overview
Alabama is now a sub-company that displays all items from Junaid (DIP + RASALKHORE) with dynamically calculated Cost and Selling Price based on two types of margins.

## Changes Made

### 1. Alabama Margins System
- **Two Margin Types:**
  - **Cost Margin**: Applied to Junaid Cost → Alabama Cost
    - Formula: `Alabama Cost = Junaid Cost × (1 + cost_margin/100)`
    - Example: If Junaid Cost = 100 AED, Cost Margin = 10%, then Alabama Cost = 110 AED
  
  - **Brand Margin**: Applied to Alabama Cost → Alabama Selling Price
    - Formula: `Alabama Selling Price = Alabama Cost / (1 - brand_margin/100)`
    - Example: If Alabama Cost = 110 AED, Brand Margin = 15%, then Selling Price = 110 / 0.85 = 129.41 AED

- **Database Table**: `alabama_margins` in `stock_data_alabama.db`
  - Stores both `cost_margin_percent` and `brand_margin_percent` per brand
  - Default margins: 10% cost margin, 15% brand margin

### 2. Admin Page: `/admin/alabama-margins`
- View and edit margins for all brands
- Set default margins (applies to brands without custom margins)
- Import margins from Excel (columns: Brand Name, Cost Margin %, Brand Margin %)
- Search and filter brands
- Case-insensitive brand matching

### 3. Alabama Stock Page Updates
- **Shows ALL items from Junaid** (DIP + RASALKHORE combined)
- **Two columns added:**
  - **Cost Price**: Calculated Alabama cost price
  - **Selling Price**: Calculated Alabama selling price
- Prices are calculated dynamically based on:
  - Junaid cost price (from DIP or RASALKHORE)
  - Cost override (if admin manually set)
  - Brand-specific margins (or defaults)

### 4. Alabama Item Detail Page
- Shows calculated Cost Price and Selling Price
- Pulls item data from DIP or RASALKHORE
- Applies Alabama margins for price calculation

### 5. Price Override Support
- Admins can still manually override cost prices for Alabama items
- Overrides are stored in `price_overrides` table in Alabama database
- Overridden cost prices are used instead of Junaid cost for calculations

## Files Modified

1. **`app.py`**:
   - Added `ensure_alabama_margins_table()` function
   - Added `get_alabama_margins()` function
   - Added `/admin/alabama-margins` route
   - Updated Alabama stock page query to pull from DIP+RASALKHORE
   - Added price calculation logic for Alabama items
   - Updated Alabama item detail page

2. **`templates/admin_alabama_margins.html`**:
   - New admin page for managing Alabama margins
   - Two-column margin input (Cost Margin + Brand Margin)
   - Excel import support

3. **`templates/stock.html`**:
   - Updated Alabama section to show Cost and Selling Price columns
   - Updated card view and table view

4. **`templates/home.html`**:
   - Added link to "Alabama Margins" in Admin section

## Usage

### Setting Up Alabama Margins

1. **Access Admin Page:**
   - Login as admin
   - Go to Home → Admin → Alabama Margins

2. **Set Default Margins:**
   - Default Cost Margin: 10% (markup on Junaid cost)
   - Default Brand Margin: 15% (margin for selling price)
   - Click "Update Defaults"

3. **Set Brand-Specific Margins:**
   - Find brand in the list
   - Edit Cost Margin % and Brand Margin %
   - Changes save automatically

4. **Import from Excel:**
   - Prepare Excel with columns:
     - Brand Name
     - Cost Margin %
     - Brand Margin %
   - Upload via "Import from Excel" section

### Viewing Alabama Stock

1. **Access Stock Page:**
   - Go to Home → Alabama (Price Only)

2. **Search Items:**
   - Search by Item Code, UPC, Description, or Manufacturer
   - All items from Junaid (DIP + RASALKHORE) are shown

3. **View Prices:**
   - **Cost Price**: Alabama cost (Junaid cost + cost margin)
   - **Selling Price**: Alabama selling price (calculated from cost + brand margin)

### Manual Price Override

1. **Override Cost Price:**
   - On Alabama stock page, click "Edit" next to Cost Price
   - Enter new cost price
   - This override will be used instead of calculated cost

## Calculation Examples

### Example 1: Default Margins
- **Junaid Cost**: 100 AED
- **Cost Margin**: 10% (default)
- **Alabama Cost**: 100 × 1.10 = **110 AED**
- **Brand Margin**: 15% (default)
- **Selling Price**: 110 / 0.85 = **129.41 AED**

### Example 2: Brand-Specific Margins
- **Brand**: COSMO
- **Junaid Cost**: 200 AED
- **Cost Margin**: 12% (custom for COSMO)
- **Alabama Cost**: 200 × 1.12 = **224 AED**
- **Brand Margin**: 20% (custom for COSMO)
- **Selling Price**: 224 / 0.80 = **280 AED**

### Example 3: With Cost Override
- **Junaid Cost**: 100 AED
- **Cost Override**: 95 AED (admin manually set)
- **Cost Margin**: 10%
- **Alabama Cost**: 95 × 1.10 = **104.50 AED** (uses override, not Junaid cost)
- **Brand Margin**: 15%
- **Selling Price**: 104.50 / 0.85 = **122.94 AED**

## Notes

- Alabama prices are calculated **dynamically** from Junaid data
- No separate sync needed for Alabama - it pulls from DIP+RASALKHORE in real-time
- Brand margins are case-insensitive (COSMO = Cosmo = cosmo)
- Admin-edited cost prices override calculated prices
- All prices are rounded to 2 decimal places

## Future Enhancements (Optional)

- Cache calculated prices in Alabama database for faster queries
- Add bulk price override import
- Add margin history/audit log
- Add price comparison view (Junaid vs Alabama)
