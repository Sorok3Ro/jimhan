#!/bin/sh
set -e

if [ -f /app/pushups.db ]; then
    chown botuser:botuser /app/pushups.db 2>/dev/null || true
    chmod 644 /app/pushups.db 2>/dev/null || true
fi

if [ -d /app/logs ]; then
    chown botuser:botuser /app/logs 2>/dev/null || true
fi

exec "$@"