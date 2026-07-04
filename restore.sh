#!/bin/bash
set -e

echo "Restoring database from latest backup..."
LATEST=$(ls -t backups/pushups_*.db 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    echo "No backups found!"
    exit 1
fi

cp "$LATEST" pushups.db
echo "Restored from: $LATEST"
