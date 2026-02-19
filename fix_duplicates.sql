-- Run on VIP: sqlite3 stock_data_headoffice.db < fix_duplicates.sql
-- Removes duplicate rows, keeping one per key.

-- 1. stock_items: keep one row per ItemCode (keep lowest rowid)
DELETE FROM stock_items WHERE rowid NOT IN (
  SELECT MIN(rowid) FROM stock_items GROUP BY "ItemCode"
);

-- 2. price_overrides: one per ItemCode
DELETE FROM price_overrides WHERE rowid NOT IN (
  SELECT MIN(rowid) FROM price_overrides GROUP BY ItemCode
);

-- 3. retail_overrides: one per (ItemCode, Branch)
DELETE FROM retail_overrides WHERE rowid NOT IN (
  SELECT MIN(rowid) FROM retail_overrides GROUP BY ItemCode, Branch
);

-- 4. brand_margins: one per brand_name
DELETE FROM brand_margins WHERE rowid NOT IN (
  SELECT MIN(rowid) FROM brand_margins GROUP BY brand_name
);

SELECT 'Done. Duplicates removed.';
