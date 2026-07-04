import asyncio
import logging
import os
import signal
import sys
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# Optional aiohttp for health check server
try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'pushups.db'
LOG_PATH = BASE_DIR / 'bot.log'
HEALTH_HOST = os.getenv('HEALTH_HOST', '0.0.0.0')
HEALTH_PORT = int(os.getenv('HEALTH_PORT', '8080'))
TZ = os.getenv('TZ', 'Europe/Moscow')

load_dotenv(BASE_DIR / '.env')

logger = logging.getLogger()
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

file_handler = RotatingFileHandler(
    LOG_PATH,
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

REDIS_DSN = os.getenv('REDIS_DSN', 'redis://localhost:6379/0')
USE_REDIS_FSM = os.getenv('USE_REDIS_FSM', 'true').lower() == 'true'

if USE_REDIS_FSM:
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(REDIS_DSN)
        logger.info(f"FSM storage: Redis ({REDIS_DSN})")
    except Exception as e:
        logger.warning(f"Redis FSM storage unavailable ({e}), falling back to in-memory MemoryStorage")
        from aiogram.fsm.storage.memory import MemoryStorage
        storage = MemoryStorage()
else:
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()
    logger.info("FSM storage: Memory (set USE_REDIS_FSM=true to enable Redis)")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Выполнил"), KeyboardButton(text="📊 Мой прогресс")],
        [KeyboardButton(text="🛌 Больничный"), KeyboardButton(text="😴 Выходной")],
        [KeyboardButton(text="🏆 Топ")],
        [KeyboardButton(text="🆘Помощь")],
        [KeyboardButton(text="❌ Удалить пользователя")]
    ],
    resize_keyboard=True
)


class Registration(StatesGroup):
    age = State()
    weight = State()
    height = State()


class DoneCount(StatesGroup):
    waiting = State()


class DeleteConfirm(StatesGroup):
    waiting = State()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            age INTEGER,
            weight REAL,
            height REAL,
            daily_norm INTEGER,
            total_done INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            max_streak INTEGER DEFAULT 0,
            last_done_date TEXT,
            sick_until TEXT,
            rest_used_today INTEGER DEFAULT 0,
            penalty_debt INTEGER DEFAULT 0,
            monthly_done INTEGER DEFAULT 0,
            monthly_norm_achieved INTEGER DEFAULT 0,
            last_rest_date TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_log (
            user_id INTEGER,
            date TEXT,
            done_count INTEGER,
            is_penalty BOOLEAN DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )
    ''')
    conn.commit()
    add_column_if_not_exists(conn, cur, 'users', 'last_rest_date', 'TEXT')
    conn.close()


def add_column_if_not_exists(conn, cur, table, column, column_type):
    cur.execute(f"PRAGMA table_info({table})")
    columns = [row['name'] for row in cur.fetchall()]
    if column not in columns:
        try:
            cur.execute(f'ALTER TABLE {table} ADD COLUMN {column} {column_type}')
            conn.commit()
            logger.info(f"Added column {column} to {table}")
        except sqlite3.OperationalError as e:
            logger.warning(f"Could not add column {column}: {e}")


init_db()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


COLS = [
    'user_id', 'username', 'age', 'weight', 'height', 'daily_norm',
    'total_done', 'current_streak', 'max_streak', 'last_done_date',
    'sick_until', 'rest_used_today', 'penalty_debt', 'monthly_done',
    'monthly_norm_achieved', 'last_rest_date'
]

ALLOWED_UPDATE_FIELDS = set(COLS)


def row_to_dict(row):
    return dict(zip(COLS, row))


def get_user(user_id):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None


def update_user(user_id, **kwargs):
    if not all(k in ALLOWED_UPDATE_FIELDS for k in kwargs):
        raise ValueError(f"Attempt to update disallowed field(s): {set(kwargs) - ALLOWED_UPDATE_FIELDS}")
    with get_conn() as conn:
        cur = conn.cursor()
        set_clause = ', '.join([f"{k} = ?" for k in kwargs])
        values = list(kwargs.values()) + [user_id]
        cur.execute(f'UPDATE users SET {set_clause} WHERE user_id = ?', values)
        conn.commit()


def create_user(user_id, username, age, weight, height):
    daily_norm = int(weight / 10 + age / 5 + height / 100) + 5
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO users (user_id, username, age, weight, height, daily_norm)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, age, weight, height, daily_norm))
        conn.commit()
    return daily_norm


def log_done(user_id, date, count, is_penalty=False):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT done_count FROM daily_log WHERE user_id = ? AND date = ?', (user_id, date))
        row = cur.fetchone()
        if row:
            new_count = row[0] + count
            cur.execute('UPDATE daily_log SET done_count = ?, is_penalty = ? WHERE user_id = ? AND date = ?',
                        (new_count, 1 if is_penalty else 0, user_id, date))
        else:
            cur.execute('INSERT INTO daily_log (user_id, date, done_count, is_penalty) VALUES (?, ?, ?, ?)',
                        (user_id, date, count, 1 if is_penalty else 0))
        conn.commit()


def get_today_log(user_id):
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT done_count FROM daily_log WHERE user_id = ? AND date = ?', (user_id, today))
        row = cur.fetchone()
        return row[0] if row else None


def get_total_done(user_id):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT SUM(done_count) FROM daily_log WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        return row[0] if row[0] else 0


def get_monthly_done(user_id):
    today = datetime.date.today()
    first_day = today.replace(day=1).isoformat()
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT SUM(done_count) FROM daily_log WHERE user_id = ? AND date >= ? AND date < ?',
                    (user_id, first_day, next_month))
        row = cur.fetchone()
        return row[0] if row[0] else 0


def get_streak(user_id):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT date, done_count FROM daily_log WHERE user_id = ? ORDER BY date DESC', (user_id,))
        rows = cur.fetchall()
    if not rows:
        return 0
    streak = 0
    today = datetime.date.today()
    for row in rows:
        log_date = datetime.date.fromisoformat(row[0])
        if row[1] <= 0:
            continue
        if (today - log_date).days == streak:
            streak += 1
        else:
            break
    return streak


async def check_and_penalize():
    today = datetime.date.today().isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT user_id, daily_norm, sick_until, penalty_debt FROM users')
        rows = cur.fetchall()
        for row in rows:
            user_id, norm, sick_until, debt = row
            if sick_until and datetime.date.today() <= datetime.date.fromisoformat(sick_until):
                continue
            today_log = get_today_log(user_id)
            if today_log is not None:
                continue
            penalty = int(norm * 1.5)
            new_debt = debt + penalty
            update_user(user_id, penalty_debt=new_debt)
            log_done(user_id, today, -penalty, is_penalty=True)


async def reset_monthly_counters():
    today = datetime.date.today()
    if today.day == 1:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute('UPDATE users SET monthly_done = 0, monthly_norm_achieved = 0')
            conn.commit()


@dp.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    if user:
        await message.answer(
            f"С возвращением, {message.from_user.first_name}!\n"
            f"Твоя норма на сегодня: {user['daily_norm']} отжиманий.\n"
            f"Всего сделано: {get_total_done(user_id)}.",
            reply_markup=main_kb
        )
        await state.clear()
    else:
        await message.answer(
            "Привет! Давай зарегистрируемся.\n"
            "Сколько тебе лет? (только число)"
        )
        await state.set_state(Registration.age)


@dp.message(F.text == "🆘Помощь")
@dp.message(Command('help'))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 Инструкция по использованию бота\n\n"
        "Добро пожаловать в челлендж по отжиманиям! 💪\n\n"
        "🔹 Ежедневная норма\n"
        "Рассчитывается автоматически на основе твоего возраста, веса и роста.\n"
        "Каждый день нужно выполнить эту норму, чтобы прогрессировать.\n\n"
        "🔹 Как отметить выполнение\n"
        "Нажми кнопку «✅ Выполнил» или введи команду /done.\n"
        "Бот попросит ввести количество отжиманий, которые ты сделал сегодня.\n"
        "Если сделал больше нормы – молодчина, это идёт в общую копилку!\n\n"
        "🔹 Пропуски по уважительной причине\n"
        "Если заболел – нажми «🛌 Больничный» или введи /sick.\n"
        "Ты освобождаешься от отжиманий на 3 дня. Прогресс (стрик) не сбрасывается.\n\n"
        "🔹 Выходной\n"
        "Один раз в неделю можно взять законный выходной – кнопка «😴 Выходной» или /rest.\n"
        "Стрик сохраняется, но не увеличивается. В этот день отжимания не нужны.\n\n"
        "🔹 Штрафы\n"
        "Если ты не отметился за день (и не на больничном, и не в выходной) –\n"
        "в 23:59 бот начислит штрафной долг в размере 1.5 × твоя норма.\n"
        "Долг можно посмотреть в прогрессе, его нужно будет компенсировать дополнительными отжиманиями позже.\n\n"
        "🔹 Мой прогресс\n"
        "Нажми «📊 Мой прогресс» или /progress – увидишь всё о своих достижениях.\n\n"
        "🔹 Топ игроков\n"
        "Кнопка «🏆 Топ» или /top – тройка лидеров по общему количеству и по стрику.\n\n"
        "🔹 Медали и достижения\n"
        "Бот выдаёт медали за стрики (7, 30, 100 дней) и за общее количество (100, 500, 1000, 5000).\n\n"
        "🎯 Главное – регулярность! Удачи в челлендже!"
    )
    await message.answer(help_text, reply_markup=main_kb, parse_mode="Markdown")


@dp.message(Registration.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Пожалуйста, введи число (возраст).")
        return
    age = int(message.text)
    if not (5 <= age <= 120):
        await message.answer("Возраст должен быть от 5 до 120 лет.")
        return
    await state.update_data(age=age)
    await message.answer("Теперь укажи свой вес (в кг, например 75.5):")
    await state.set_state(Registration.weight)


@dp.message(Registration.weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("Введи вес числом (например 75.5).")
        return
    if not (20 <= weight <= 500):
        await message.answer("Вес должен быть от 20 до 500 кг.")
        return
    await state.update_data(weight=weight)
    await message.answer("И рост (в см, например 180):")
    await state.set_state(Registration.height)


@dp.message(Registration.height)
async def process_height(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Введи рост числом (в см).")
        return
    height = int(message.text)
    if not (50 <= height <= 250):
        await message.answer("Рост должен быть от 50 до 250 см.")
        return
    data = await state.get_data()
    age = data['age']
    weight = data['weight']
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    daily_norm = create_user(user_id, username, age, weight, height)
    await message.answer(
        f"Регистрация завершена!\n"
        f"Твоя ежедневная норма: {daily_norm} отжиманий.\n"
        f"Используй кнопки или команды для взаимодействия.",
        reply_markup=main_kb
    )
    await state.clear()


@dp.message(F.text == "✅ Выполнил")
@dp.message(Command('done'))
async def cmd_done(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйся через /start")
        return

    sick_until = user['sick_until']
    if sick_until and datetime.date.today() <= datetime.date.fromisoformat(sick_until):
        await message.answer(f"Ты на больничном до {sick_until}, отдыхай!")
        return

    await message.answer("Сколько отжиманий ты сделал сегодня? (введи число)")
    await state.set_state(DoneCount.waiting)


@dp.message(DoneCount.waiting, F.text)
async def handle_done_count(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Введи целое число.")
        return

    count = int(message.text)
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйся.")
        await state.clear()
        return

    today = datetime.date.today().isoformat()
    today_log = get_today_log(user_id)
    log_done(user_id, today, count)

    new_total = user['total_done'] + count
    new_monthly = get_monthly_done(user_id)
    last_done = user['last_done_date']
    if today_log is None:
        if last_done:
            last_date = datetime.date.fromisoformat(last_done)
            if (datetime.date.today() - last_date).days == 1:
                new_streak = user['current_streak'] + 1
            else:
                new_streak = 1
        else:
            new_streak = 1
        max_streak = max(user['max_streak'], new_streak)
        update_user(user_id,
                    total_done=new_total,
                    current_streak=new_streak,
                    max_streak=max_streak,
                    last_done_date=today)
    else:
        new_streak = user['current_streak']
        max_streak = user['max_streak']
        update_user(user_id, total_done=new_total)

    monthly_norm = user['daily_norm'] * 30
    monthly_achieved = user['monthly_norm_achieved']
    awards = []
    if new_monthly >= monthly_norm and monthly_achieved == 0:
        awards.append("🏅 Месячный воин! Закрыл месячную норму!")
        update_user(user_id, monthly_norm_achieved=1)
    elif new_monthly >= monthly_norm * 2 and monthly_achieved == 1:
        awards.append("🏅 Месячный гигант! Двойная норма за месяц!")
        update_user(user_id, monthly_norm_achieved=2)

    medals = []
    if new_streak == 7:
        medals.append("🏅 Железная медаль (7 дней стрика)")
    if new_streak == 30:
        medals.append("🏅 Титан (30 дней стрика)")
    if new_streak == 100:
        medals.append("🏅 Бессмертный (100 дней стрика)")

    if new_total >= 100 and user['total_done'] < 100:
        medals.append("🏅 100 отжиманий суммарно")
    if new_total >= 500 and user['total_done'] < 500:
        medals.append("🏅 500 отжиманий")
    if new_total >= 1000 and user['total_done'] < 1000:
        medals.append("🏅 1000 отжиманий")
    if new_total >= 5000 and user['total_done'] < 5000:
        medals.append("🏅 5000 отжиманий (Легенда!)")

    reply = f"Отлично! Ты сделал {count} отжиманий.\n"
    reply += f"Всего сделано: {new_total}\n"
    reply += f"За месяц: {new_monthly}/{monthly_norm}\n"
    reply += f"Текущий стрик: {new_streak} дней (макс: {max_streak})"
    if medals or awards:
        reply += "\n\n🎉 Новые достижения:\n" + "\n".join(medals + awards)
    await message.answer(reply, reply_markup=main_kb)
    await state.clear()


@dp.message(F.text == "🛌 Больничный")
@dp.message(Command('sick'))
async def cmd_sick(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйся.")
        return
    sick_until = (datetime.date.today() + timedelta(days=3)).isoformat()
    update_user(user_id, sick_until=sick_until)
    await message.answer(f"Больничный оформлен. Ты освобождён от отжиманий до {sick_until} (включительно).")


@dp.message(F.text == "😴 Выходной")
@dp.message(Command('rest'))
async def cmd_rest(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйся.")
        return

    last_rest = user.get('last_rest_date')
    if last_rest:
        last_rest_date = datetime.date.fromisoformat(last_rest)
        if (datetime.date.today() - last_rest_date).days < 7:
            await message.answer("Ты уже брал выходной на этой неделе. Подожди 7 дней.")
            return

    today = datetime.date.today().isoformat()
    log_done(user_id, today, 0)
    update_user(user_id, last_rest_date=today)
    await message.answer("Выходной засчитан! Сегодня отдыхаешь, стрик сохраняется.")


@dp.message(F.text == "📊 Мой прогресс")
@dp.message(Command('progress'))
async def cmd_progress(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Сначала зарегистрируйся.")
        return
    total = get_total_done(user_id)
    streak = get_streak(user_id)
    norm = user['daily_norm']
    debt = user['penalty_debt']
    monthly_done = get_monthly_done(user_id)
    monthly_norm = norm * 30
    monthly_achieved = user['monthly_norm_achieved']
    today_log = get_today_log(user_id)
    if today_log is None:
        today_done = "не отмечено"
    else:
        today_done = str(today_log) + (" (штраф)" if today_log < 0 else "") + (" (выходной)" if today_log == 0 else "")

    medals = []
    if streak == 7:
        medals.append("🏅 Железная медаль (7 дней стрика)")
    if streak == 30:
        medals.append("🏅 Титан (30 дней стрика)")
    if streak == 100:
        medals.append("🏅 Бессмертный (100 дней стрика)")
    if total >= 100:
        medals.append("🏅 100 отжиманий суммарно")
    if total >= 500:
        medals.append("🏅 500 отжиманий")
    if total >= 1000:
        medals.append("🏅 1000 отжиманий")
    if total >= 5000:
        medals.append("🏅 5000 отжиманий (Легенда!)")
    if monthly_achieved >= 2:
        medals.append("🏅 Месячный гигант! Двойная норма за месяц!")
    elif monthly_achieved >= 1:
        medals.append("🏅 Месячный воин! Закрыл месячную норму!")

    reply = (
        f"📊 Твой прогресс:\n"
        f"Норма на день: {norm}\n"
        f"Сегодня сделано: {today_done}\n"
        f"Всего сделано: {total}\n"
        f"За месяц: {monthly_done}/{monthly_norm}"
    )
    if monthly_achieved >= 2:
        reply += " (двойная норма!)"
    elif monthly_achieved >= 1:
        reply += " ✅"
    reply += f"\nТекущий стрик: {streak} дней\n"
    reply += f"Макс. стрик: {user['max_streak']}\n"
    reply += f"Штрафной долг: {debt} отжиманий"
    if medals:
        reply += "\n\n🎖 Твои достижения:\n" + "\n".join(medals)
    await message.answer(reply)


@dp.message(F.text == "🏆 Топ")
@dp.message(Command('top'))
async def cmd_top(message: types.Message):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT username, total_done FROM users ORDER BY total_done DESC LIMIT 3
        ''')
        top_total = cur.fetchall()
        cur.execute('''
            SELECT username, current_streak FROM users ORDER BY current_streak DESC LIMIT 3
        ''')
        top_streak = cur.fetchall()

    reply = "🏆 Топ-3 по силе (всего):\n"
    for i, (name, val) in enumerate(top_total, 1):
        reply += f"{i}. {name or 'Аноним'} — {val} отжиманий\n"

    reply += "\n🏆 Топ-3 по дисциплине (стрик):\n"
    for i, (name, val) in enumerate(top_streak, 1):
        reply += f"{i}. {name or 'Аноним'} — {val} дней\n"

    await message.answer(reply)


@dp.message(Command('delete_me'))
async def cmd_delete_me(message: types.Message, state: FSMContext):
    args = message.text.strip().split(maxsplit=1)
    if len(args) == 2 and args[1].lower() == 'confirm':
        data = await state.get_data()
        if not data.get('delete_confirm'):
            await message.answer("Сначала отправь /delete_me для подтверждения.")
            return

        user_id = message.from_user.id
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM daily_log WHERE user_id = ?', (user_id,))
            cur.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            conn.commit()

        await message.answer(
            "✅ Все твои данные удалены. Ты можешь заново зарегистрироваться через /start.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        return

    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("Ты ещё не зарегистрирован. Ничего удалять не нужно.")
        return

    await message.answer(
        "⚠️ Ты действительно хочешь удалить все свои данные?\n"
        "Это действие необратимо! Будут удалены:\n"
        "- твой профиль,\n"
        "- вся история отжиманий,\n"
        "- стрики и медали.\n\n"
        "Если уверен, отправь команду ещё раз: /delete_me confirm"
    )
    await state.update_data(delete_confirm=True)


if HAS_AIOHTTP:
    async def health_handler(request):
        return web.Response(text='OK')

    async def start_health_server():
        app = web.Application()
        app.router.add_get('/health', health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HEALTH_HOST, HEALTH_PORT)
        await site.start()
        logger.info(f"Health check server started on {HEALTH_HOST}:{HEALTH_PORT}/health")
else:
    async def start_health_server():
        logger.warning("aiohttp not installed, health check server disabled")


async def on_startup():
    logger.info("Bot started")
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Log file: {LOG_PATH}")
    logger.info(f"Timezone: {TZ}")


async def on_shutdown():
    logger.info("Bot shutting down...")
    if hasattr(storage, 'close'):
        try:
            await storage.close()
            logger.info("Storage closed")
        except Exception as e:
            logger.error(f"Error closing storage: {e}")
    logger.info("Bot stopped")


def handle_sigterm(signum, frame):
    logger.info("Received SIGTERM/SIGINT, shutting down...")
    sys.exit(0)


async def main():
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_penalize, CronTrigger(hour=23, minute=59, timezone=TZ))
    scheduler.add_job(reset_monthly_counters, CronTrigger(hour=0, minute=0, timezone=TZ))
    scheduler.start()
    logger.info("Scheduler started")

    await start_health_server()

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Polling error: {e}", exc_info=True)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
