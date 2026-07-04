# Project Context: Pushups Challenge Bot

## Overview
Telegram bot for push-up challenge tracking. Users register with age/weight/height, get a daily norm, log completed push-ups, and compete on leaderboards. Features streaks, medals, sick leave, rest days, and penalty debts for missed days.

## Tech Stack
- **Language**: Python 3.10+
- **Framework**: aiogram v3
- **Database**: SQLite (`pushups.db`)
- **Scheduler**: APScheduler (cron jobs for penalties at 23:59 and monthly reset at 00:00)
- **Config**: `.env` with `BOT_TOKEN`

## Project Structure
- `bot.py` — monolith: handlers, DB layer, FSM states, scheduler setup in one file

## Current State
- FSM flows: Registration (age → weight → height), DoneCount (waiting for count)
- Main keyboard: ✅ Выполнил, 📊 Мой прогресс, 🛌 Больничный, 😴 Выходной, 🏆 Топ, 🆘Помощь, ❌ Удалить пользователя
- DB tables: `users` (profile + stats), `daily_log` (per-day push-up counts, supports penalties via negative counts)

## Recent Changes (2026-07-04)
- Fixed indentation error in `get_user()`
- Fixed broken `/delete_me confirm` (Command filter doesn't parse args; now manual split)
- Refactored DB access to `with get_conn() as conn` + `sqlite3.Row`
- `monthly_done` now computed dynamically via `get_monthly_done()` to stay in sync with penalties
- `get_streak()` skips days with `done_count <= 0` so penalties/rest days don't break streaks
- Added separate `last_rest_date` column and migration in `init_db` for rest-day tracking
- Added input validation: age 5–120, weight 20–500 kg, height 50–250 cm
- Added medals/awards output to `/progress`
- Added `scheduler.shutdown(wait=False)` in `main()` try/finally
- Added file logging with `RotatingFileHandler` (`bot.log`, 5 MB × 3 backups)
- Enabled SQLite WAL mode (`journal_mode=WAL`, `synchronous=NORMAL`) to reduce write contention
- Added robust column migration helper `add_column_if_not_exists` for safe schema updates
- **Redis 7 integrated**: FSM uses `RedisStorage` by default when reachable. Falls back to `MemoryStorage` otherwise.
- Added `TZ` env var and timezone-aware APScheduler jobs
- Added aiohttp health check server (`/health` on port 8080)
- Added graceful shutdown for scheduler, storage, and health server
- Added production Docker Compose (`docker-compose.prod.yml`) with resource limits, health checks, read-only rootfs
- Added `Dockerfile` with non-root user and curl for health checks
- Added backup/restore scripts (`backup.sh`, `restore.sh`)
- Added `DEPLOY.md` with server deployment instructions

## Known Issues / TODOs
- `reset_monthly_counters` resets `monthly_norm_achieved` but users already at 2x will lose state
- `monthly_norm_achieved` is a simple int (0/1/2); no persistent medal registry beyond this
- If `aiogram[redis]` is not installed or Redis is unreachable, bot falls back to `MemoryStorage` (FSM state lost on restart)
- No tests, no CI
- Bot token hardcoded expectation via `.env`; no fallback config sources
- `help_text` uses Markdown but `cmd_help` reply_markup shows even on help (minor UX)

## How to Run Locally
```powershell
# Install deps
pip install -r requirements.txt

# Copy env
cp .env.example .env

# Set BOT_TOKEN in .env, then run
python bot.py
```

## Production Deployment (Single Server)
See `DEPLOY.md`. Quick summary:
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
- Health check on `http://<host>:8080/health`
- Backup via `./backup.sh` (daily cron recommended)

## Requirements
- CPU: 1 vCPU
- RAM: 512 MB minimum (bot + Redis)
- Disk: 10 GB (SQLite DB + logs + Redis dump)
- Network: Outbound HTTPS (443) to Telegram API

## Logs
- Console: stdout
- File: `bot.log` (rotating, 5 MB × 5 backups)

## How to Test
- Register: `/start` → age → weight → height
- Log push-ups: ✅ Выполнил → enter count
- Sick leave: 🛌 Больничный (3 days)
- Rest day: 😴 Выходной (once per 7 days, tracked by `last_rest_date`)
- Progress: 📊 Мой прогресс
- Leaderboard: 🏆 Топ
- Delete account: ❌ Удалить пользователя → `/delete_me confirm`
