#!/bin/sh
set -e

mkdir -p /app/logs
chown botuser:botuser /app/logs 2>/dev/null || true
chmod 755 /app/logs 2>/dev/null || true

if [ -d /app/pushups.db ]; then
    rm -rf /app/pushups.db
fi

if [ ! -f /app/pushups.db ]; then
    touch /app/pushups.db
fi

chown botuser:botuser /app/pushups.db 2>/dev/null || true
chmod 644 /app/pushups.db 2>/dev/null || true

if [ ! -f /app/logs/bot.log ]; then
    touch /app/logs/bot.log
fi

chown botuser:botuser /app/logs/bot.log 2>/dev/null || true
chmod 644 /app/logs/bot.log 2>/dev/null || true

if [ -f /app/.env ]; then
    chown botuser:botuser /app/.env 2>/dev/null || true
    chmod 644 /app/.env 2>/dev/null || true
fi

exec "$@"