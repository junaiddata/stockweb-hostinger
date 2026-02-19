-- Safe duplicate fix: only run on tables that exist.
-- Usage: sqlite3 stock_data_headoffice.db < fix_duplicates_safe.sql

-- Show tables first
.tables

-- Remove duplicates only if table exists (run these one by one if needed)
DELETE FROM stock_items WHERE rowid NOT IN (SELECT MIN(rowid) FROM stock_items GROUP BY "ItemCode");
DELETE FROM price_overrides WHERE rowid NOT IN (SELECT MIN(rowid) FROM price_overrides GROUP BY ItemCode);
DELETE FROM retail_overrides WHERE rowid NOT IN (SELECT MIN(rowid) FROM retail_overrides GROUP BY ItemCode, Branch);
DELETE FROM brand_margins WHERE rowid NOT IN (SELECT MIN(rowid) FROM brand_margins GROUP BY brand_name);
SELECT 'Done.';
