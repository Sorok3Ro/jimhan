#!/bin/sh
set -e

mkdir -p /app/logs
chown botuser:botuser /app/logs 2>/dev/null || true
chmod 755 /app/logs 2>/dev/null || true

if [ -f /app/pushups.db ]; then
    chown botuser:botuser /app/pushups.db 2>/dev/null || true
    chmod 644 /app/pushups.db 2>/dev/null || true
fi

touch /app/logs/bot.log
chown botuser:botuser /app/logs/bot.log 2>/dev/null || true
chmod 644 /app/logs/bot.log 2>/dev/null || true

if [ -f /app/.env ]; then
    chown botuser:botuser /app/.env 2>/dev/null || true
    chmod 644 /app/.env 2>/dev/null || true
fi

exec "$@"