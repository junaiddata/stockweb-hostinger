#!/bin/bash
# Daily backup script for Stock Web databases.
# Usage: bash backup_dbs.sh
# Cron:  0 2 * * * /var/www/stockweb/backup_dbs.sh >> /var/log/stockweb_backup.log 2>&1

set -e

APP_DIR="/var/www/stockweb"
BACKUP_ROOT="/root/stockweb_backups"
TODAY=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$TODAY"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Starting backup..."

# 1. Copy all database files
for db in stock_data_headoffice.db stock_data_rasalkhor.db stock_data_alabama.db devices.db; do
    src="$APP_DIR/$db"
    if [ -f "$src" ]; then
        cp "$src" "$BACKUP_DIR/"
        echo "  Copied $db ($(du -h "$src" | cut -f1))"
    fi
done

# 2. Export override tables as SQL (easy to restore individually)
if [ -f "$APP_DIR/stock_data_headoffice.db" ]; then
    for tbl in price_overrides brand_margins retail_overrides cost_price_overrides; do
        sqlite3 "$APP_DIR/stock_data_headoffice.db" ".dump $tbl" > "$BACKUP_DIR/${tbl}.sql" 2>/dev/null || true
    done
    echo "  Exported override tables as SQL"
fi

if [ -f "$APP_DIR/stock_data_alabama.db" ]; then
    for tbl in price_overrides alabama_margins; do
        sqlite3 "$APP_DIR/stock_data_alabama.db" ".dump $tbl" > "$BACKUP_DIR/alabama_${tbl}.sql" 2>/dev/null || true
    done
    echo "  Exported Alabama override tables"
fi

# 3. Integrity check
for db in stock_data_headoffice.db stock_data_rasalkhor.db stock_data_alabama.db; do
    result=$(sqlite3 "$BACKUP_DIR/$db" "PRAGMA integrity_check;" 2>/dev/null || echo "FAILED")
    echo "  Integrity $db: $result"
done

# 4. Remove backups older than KEEP_DAYS
find "$BACKUP_ROOT" -maxdepth 1 -type d -mtime +$KEEP_DAYS -exec rm -rf {} \; 2>/dev/null || true

echo "[$(date)] Backup complete: $BACKUP_DIR"
ls -lh "$BACKUP_DIR"
