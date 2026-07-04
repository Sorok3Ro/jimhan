# Production Deployment Guide

## Prerequisites on Server
- Docker Engine 20.10+
- Docker Compose 2.0+
- Linux OS (Ubuntu/Debian recommended)
- Open ports: 80/443 (if using reverse proxy), 6379 only for internal Docker network

## Quick Deploy
```bash
git clone <repo-url> /opt/pushups-bot && cd /opt/pushups-bot
cp .env.example .env
nano .env  # Set BOT_TOKEN, optionally REDIS_DSN
docker compose up -d --build
```

## Verify Deployment
```bash
# Check container status
docker compose ps

# Check logs
docker compose logs -f bot

# Health check
curl http://localhost:8080/health
# Expected: OK

# Check Redis
docker compose exec redis redis-cli ping
# Expected: PONG
```

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | required | Telegram bot token from @BotFather |
| `REDIS_DSN` | `redis://redis:6379/0` | Redis DSN (use `redis://redis:6379/0` in Docker) |
| `USE_REDIS_FSM` | `true` | Enable Redis FSM storage |
| `TZ` | `Europe/Moscow` | Timezone for scheduler and dates |
| `HEALTH_HOST` | `0.0.0.0` | Health check bind address |
| `HEALTH_PORT` | `8080` | Health check port |

## Important Notes
- **Timezone**: Set `TZ` in `.env` to your local timezone. APScheduler jobs (penalties at 23:59, monthly reset at 00:00) use this timezone.
- **Redis**: In Docker Compose, Redis is accessible from bot container via hostname `redis`. Do not expose port 6379 to public internet.
- **Database**: `pushups.db` is stored on host volume. Backup it regularly (see `backup.sh`).
- **Logs**: `bot.log` rotates automatically (5 MB × 5 backups). Keep it on host for debugging.

## Backup & Restore
```bash
# Manual backup
./backup.sh

# Restore from latest backup
./restore.sh
```

## Zero-Downtime Update
```bash
cd /opt/pushups-bot
git pull
docker compose up -d --build --no-deps bot
```

## Monitoring
- Health endpoint: `http://<server-ip>:8080/health`
- Container logs: `docker compose logs -f bot redis`
- Set up external monitoring (UptimeRobot, Healthchecks.io) against `/health`

## Troubleshooting
| Issue | Solution |
|---|---|
| Bot won't start, Redis connection refused | Ensure Redis container is healthy: `docker compose ps redis` |
| Scheduler runs at wrong time | Check server timezone: `timedatectl`. Set `TZ` in `.env`. |
| Database locked | SQLite WAL mode handles most cases. If persists, restart bot container. |
| FSM state lost after restart | Ensure `USE_REDIS_FSM=true` and Redis is reachable. |
