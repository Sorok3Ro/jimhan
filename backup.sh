#!/bin/bash
set -euo pipefail

BACKUP_DIR="/opt/pushups-bot/backups"
DB_FILE="/opt/pushups-bot/pushups.db"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/pushups_$TIMESTAMP.db"

cp "$DB_FILE" "$BACKUP_FILE"

find "$BACKUP_DIR" -name "pushups_*.db" -type f -mtime +$RETENTION_DAYS -delete

echo "Backup created: $BACKUP_FILE"
