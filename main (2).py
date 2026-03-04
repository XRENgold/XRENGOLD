import asyncio
import logging
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import random
import string

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "8613106459:AAH5mNQV6g4jdVJt_IhdDjzQ2IzCy2dkXPI"
ADMIN_IDS = [5060774905]
REFERRAL_BONUS = 1.5  # Бонус за реферала
MIN_WITHDRAW = 10  # Минимальная сумма вывода
# ==============================================

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ================== БАЗА ДАННЫХ ==================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        # Таблица пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                total_earned REAL DEFAULT 0,
                referrer_id INTEGER,
                referrals_count INTEGER DEFAULT 0,
                joined_date TEXT
            )
        ''')

        # Таблица рефералов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referral_id INTEGER,
                date TEXT
            )
        ''')

        # Таблица обязательных каналов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT,
                channel_username TEXT,
                channel_title TEXT,
                added_date TEXT
            )
        ''')

        # Таблица промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                amount REAL,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0
            )
        ''')

        # Таблица использованных промокодов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocode_uses (
                user_id INTEGER,
                code TEXT,
                date TEXT
            )
        ''')

        # Таблица заданий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                description TEXT,
                reward REAL,
                type TEXT,
                target TEXT,
                created_date TEXT
            )
        ''')

        # Таблица выполненных заданий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                date TEXT,
                PRIMARY KEY (user_id, task_id)
            )
        ''')

        # Таблица заявок на вывод
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                date TEXT
            )
        ''')

        self.conn.commit()

    # ===== Управление каналами =====
    def add_channel(self, channel_id, channel_username, channel_title):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT OR IGNORE INTO required_channels (channel_id, channel_username, channel_title, added_date)
            VALUES (?, ?, ?, ?)
        ''', (str(channel_id), channel_username, channel_title, date))
        self.conn.commit()
        return self.cursor.lastrowid

    def remove_channel(self, channel_id):
        self.cursor.execute('DELETE FROM required_channels WHERE channel_id = ?', (str(channel_id),))
        self.conn.commit()

    def get_channels(self):
        self.cursor.execute('SELECT * FROM required_channels')
        return self.cursor.fetchall()

    def channel_exists(self, channel_username):
        self.cursor.execute('SELECT * FROM required_channels WHERE channel_username = ?', (channel_username,))
        return self.cursor.fetchone() is not None

    # ===== Пользователи =====
    def add_user(self, user_id, username, first_name, referrer_id=None):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, referrer_id, joined_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, referrer_id, date))
        self.conn.commit()

        # Если есть реферер, начисляем бонус
        if referrer_id:
            # Проверяем, не был ли уже начислен бонус за этого реферала
            self.cursor.execute('SELECT * FROM referrals WHERE referral_id = ?', (user_id,))
            if not self.cursor.fetchone():
                self.cursor.execute('''
                    UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, referrals_count = referrals_count + 1
                    WHERE user_id = ?
                ''', (REFERRAL_BONUS, REFERRAL_BONUS, referrer_id))

                self.cursor.execute('''
                    INSERT INTO referrals (referrer_id, referral_id, date)
                    VALUES (?, ?, ?)
                ''', (referrer_id, user_id, date))
                self.conn.commit()

    def get_user(self, user_id):
        self.cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return self.cursor.fetchone()

    def update_balance(self, user_id, amount):
        self.cursor.execute('''
            UPDATE users SET balance = balance + ?, total_earned = total_earned + ?
            WHERE user_id = ?
        ''', (amount, amount, user_id))
        self.conn.commit()

    # ===== Промокоды =====
    def create_promocode(self, code, amount, max_uses):
        self.cursor.execute('''
            INSERT INTO promocodes (code, amount, max_uses)
            VALUES (?, ?, ?)
        ''', (code.upper(), amount, max_uses))
        self.conn.commit()

    def use_promocode(self, user_id, code):
        code = code.upper()

        # Проверяем существование промокода
        self.cursor.execute('SELECT * FROM promocodes WHERE code = ?', (code,))
        promo = self.cursor.fetchone()

        if not promo:
            return False, "❌ Промокод не найден"

        # Проверяем лимит использований
        if promo[3] >= promo[2]:
            return False, "❌ Промокод больше недействителен"

        # Проверял ли пользователь уже этот промокод
        self.cursor.execute('SELECT * FROM promocode_uses WHERE user_id = ? AND code = ?', (user_id, code))
        if self.cursor.fetchone():
            return False, "❌ Вы уже использовали этот промокод"

        # Начисляем бонус
        self.cursor.execute('UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?',
                            (promo[1], promo[1], user_id))

        # Обновляем счетчик использований
        self.cursor.execute('UPDATE promocodes SET current_uses = current_uses + 1 WHERE code = ?', (code,))

        # Записываем использование
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('INSERT INTO promocode_uses (user_id, code, date) VALUES (?, ?, ?)',
                            (user_id, code, date))

        self.conn.commit()
        return True, f"✅ Промокод активирован! +{promo[1]} G"

    def get_all_promocodes(self):
        self.cursor.execute('SELECT * FROM promocodes')
        return self.cursor.fetchall()

    # ===== Задания =====
    def add_task(self, name, description, reward, type, target):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT INTO tasks (name, description, reward, type, target, created_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, description, reward, type, target, date))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_tasks(self):
        self.cursor.execute('SELECT * FROM tasks ORDER BY id DESC')
        return self.cursor.fetchall()

    def get_task(self, task_id):
        self.cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        return self.cursor.fetchone()

    def delete_task(self, task_id):
        self.cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        self.cursor.execute('DELETE FROM completed_tasks WHERE task_id = ?', (task_id,))
        self.conn.commit()

    def get_user_tasks(self, user_id):
        """Получает все задания с информацией о выполнении пользователем"""
        self.cursor.execute('''
            SELECT t.*, 
                   CASE WHEN ct.user_id IS NOT NULL THEN 1 ELSE 0 END as completed
            FROM tasks t
            LEFT JOIN completed_tasks ct ON t.id = ct.task_id AND ct.user_id = ?
            ORDER BY t.id DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    def complete_task(self, user_id, task_id):
        # Проверяем, выполнял ли пользователь задание
        self.cursor.execute('SELECT * FROM completed_tasks WHERE user_id = ? AND task_id = ?', (user_id, task_id))
        if self.cursor.fetchone():
            return False, "❌ Вы уже выполняли это задание"

        # Получаем задание
        self.cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
        task = self.cursor.fetchone()

        if not task:
            return False, "❌ Задание не найдено"

        # Проверяем тип задания
        if task[4] == "subscription":
            # Проверяем подписку на канал
            try:
                # Используем asyncio.create_task для асинхронного вызова
                member = asyncio.run_coroutine_threadsafe(
                    bot.get_chat_member(chat_id=task[5], user_id=user_id),
                    asyncio.get_event_loop()
                ).result()
                if member.status == 'left':
                    return False, "❌ Вы не подписаны на канал"
            except Exception as e:
                return False, "❌ Ошибка проверки подписки"

        # Начисляем награду
        self.cursor.execute('UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?',
                            (task[3], task[3], user_id))

        # Записываем выполнение
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('INSERT INTO completed_tasks (user_id, task_id, date) VALUES (?, ?, ?)',
                            (user_id, task_id, date))

        self.conn.commit()
        return True, f"✅ Задание выполнено! +{task[3]} G"

    # ===== Вывод средств =====
    def create_withdraw_request(self, user_id, amount, wallet):
        # Проверяем баланс
        user = self.get_user(user_id)
        if user[3] < amount:
            return False, "❌ Недостаточно средств"

        if amount < MIN_WITHDRAW:
            return False, f"❌ Минимальная сумма вывода {MIN_WITHDRAW} G"

        # Списываем средства
        self.cursor.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))

        # Создаем заявку
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute('''
            INSERT INTO withdrawals (user_id, amount, wallet, date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, amount, wallet, date))
        self.conn.commit()

        # Получаем ID созданной заявки
        withdrawal_id = self.cursor.lastrowid

        return True, "✅ Заявка на вывод создана! Ожидайте подтверждения", withdrawal_id

    def get_pending_withdrawals(self):
        self.cursor.execute('SELECT * FROM withdrawals WHERE status = "pending" ORDER BY date ASC')
        return self.cursor.fetchall()

    def get_withdrawal(self, withdrawal_id):
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        return self.cursor.fetchone()

    def complete_withdrawal(self, withdrawal_id):
        self.cursor.execute('UPDATE withdrawals SET status = "completed" WHERE id = ?', (withdrawal_id,))
        self.conn.commit()

        # Получаем информацию о выводе
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        return self.cursor.fetchone()

    def reject_withdrawal(self, withdrawal_id):
        # Получаем информацию о выводе
        self.cursor.execute('SELECT * FROM withdrawals WHERE id = ?', (withdrawal_id,))
        withdrawal = self.cursor.fetchone()

        if withdrawal:
            # Возвращаем средства
            self.cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?',
                                (withdrawal[2], withdrawal[1]))
            self.cursor.execute('UPDATE withdrawals SET status = "rejected" WHERE id = ?', (withdrawal_id,))
            self.conn.commit()
            return withdrawal
        return None

    # ===== Рефералы =====
    def get_referrals(self, user_id):
        self.cursor.execute('''
            SELECT u.user_id, u.username, u.first_name, r.date 
            FROM referrals r
            JOIN users u ON r.referral_id = u.user_id
            WHERE r.referrer_id = ?
            ORDER BY r.date DESC
        ''', (user_id,))
        return self.cursor.fetchall()

    # ===== Статистика =====
    def get_total_users(self):
        self.cursor.execute('SELECT COUNT(*) FROM users')
        return self.cursor.fetchone()[0]

    def get_total_balance(self):
        self.cursor.execute('SELECT SUM(balance) FROM users')
        result = self.cursor.fetchone()[0]
        return result if result else 0

    def get_total_tasks_completed(self):
        self.cursor.execute('SELECT COUNT(*) FROM completed_tasks')
        return self.cursor.fetchone()[0]


# Инициализация базы данных
db = Database()


# ================== FSM СОСТОЯНИЯ ==================
class AdminStates(StatesGroup):
    # Для промокодов
    waiting_for_promocode = State()
    waiting_for_promocode_amount = State()
    waiting_for_promocode_uses = State()

    # Для заданий
    waiting_for_task_name = State()
    waiting_for_task_desc = State()
    waiting_for_task_reward = State()
    waiting_for_task_target = State()
    waiting_for_task_delete = State()

    # Для каналов
    waiting_for_channel = State()

    # Для вывода
    waiting_for_withdraw_id = State()


class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_wallet = State()


# ================== КЛАВИАТУРЫ ==================
def get_main_keyboard():
    """Главная клавиатура для пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="👥 Рефералы"), KeyboardButton(text="🎁 Задания"), KeyboardButton(text="💳 Вывод")],
            [KeyboardButton(text="🔗 Реферальная ссылка"), KeyboardButton(text="📢 Каналы"),
             KeyboardButton(text="❓ Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_admin_main_keyboard():
    """Главная клавиатура для админа"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="👥 Рефералы"), KeyboardButton(text="🎁 Задания"), KeyboardButton(text="💳 Вывод")],
            [KeyboardButton(text="🔗 Реферальная ссылка"), KeyboardButton(text="📢 Каналы"),
             KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="👑 Админ панель")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_back_keyboard():
    """Клавиатура с кнопкой назад"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад в меню")]],
        resize_keyboard=True
    )
    return keyboard


def get_admin_panel_keyboard():
    """Клавиатура админ-панели"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Управление каналами")],
            [KeyboardButton(text="🎫 Управление промокодами")],
            [KeyboardButton(text="📝 Управление заданиями")],
            [KeyboardButton(text="📋 Заявки на вывод")],
            [KeyboardButton(text="📊 Статистика бота")],
            [KeyboardButton(text="🔙 Назад в меню")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_channels_management_keyboard():
    """Клавиатура управления каналами"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить канал")],
            [KeyboardButton(text="➖ Удалить канал")],
            [KeyboardButton(text="📋 Список каналов")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_promocodes_management_keyboard():
    """Клавиатура управления промокодами"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать промокод")],
            [KeyboardButton(text="📋 Список промокодов")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_tasks_management_keyboard():
    """Клавиатура управления заданиями"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать задание")],
            [KeyboardButton(text="➖ Удалить задание")],
            [KeyboardButton(text="📋 Список заданий")],
            [KeyboardButton(text="🔙 Назад в админку")]
        ],
        resize_keyboard=True
    )
    return keyboard


# ================== ПРОВЕРКА ПОДПИСКИ ==================
async def check_subscription(user_id):
    try:
        channels = db.get_channels()
        if not channels:  # Если нет обязательных каналов
            return True

        for channel in channels:
            try:
                member = await bot.get_chat_member(chat_id=channel[2], user_id=user_id)
                if member.status == 'left':
                    return False
            except Exception as e:
                logging.error(f"Ошибка проверки канала {channel[2]}: {e}")
                # Если канал недоступен, пропускаем его
                continue
        return True
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        return False


async def get_subscription_keyboard():
    channels = db.get_channels()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for channel in channels:
        username = channel[2].replace('@', '')
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"📢 {channel[3]}",
                url=f"https://t.me/{username}"
            )
        ])

    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ])

    return keyboard


async def subscription_required(message: types.Message):
    if not await check_subscription(message.from_user.id):
        keyboard = await get_subscription_keyboard()
        await message.answer(
            "❌ Для использования бота необходимо подписаться на наши каналы!",
            reply_markup=keyboard
        )
        return False
    return True


# ================== ХЕНДЛЕРЫ ==================

# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Нет юзернейма"
    first_name = message.from_user.first_name

    # Проверяем реферальный код
    referrer_id = None
    args = message.text.split()
    if len(args) > 1:
        try:
            referrer_id = int(args[1].split('_')[1])
            if referrer_id == user_id:
                referrer_id = None
        except (IndexError, ValueError):
            pass

    # Добавляем пользователя в базу
    db.add_user(user_id, username, first_name, referrer_id)

    # Проверяем подписку
    if not await check_subscription(user_id):
        keyboard = await get_subscription_keyboard()
        await message.answer(
            f"👋 Привет, {first_name}!\n\n"
            "Для доступа к боту нужно подписаться на наши каналы!",
            reply_markup=keyboard
        )
        return

    user = db.get_user(user_id)

    # Выбираем клавиатуру в зависимости от прав
    if user_id in ADMIN_IDS:
        keyboard = get_admin_main_keyboard()
    else:
        keyboard = get_main_keyboard()

    await message.answer(
        f"👋 Добро пожаловать, {first_name}!\n\n"
        f"💰 Твой баланс: {user[3]} G\n"
        f"👥 Приглашено друзей: {user[6]}\n\n"
        f"Используй кнопки ниже для навигации 👇",
        reply_markup=keyboard
    )


# Проверка подписки по callback
@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    if await check_subscription(callback.from_user.id):
        await callback.message.delete()
        user = db.get_user(callback.from_user.id)

        # Выбираем клавиатуру
        if callback.from_user.id in ADMIN_IDS:
            keyboard = get_admin_main_keyboard()
        else:
            keyboard = get_main_keyboard()

        await callback.message.answer(
            f"✅ Подписка подтверждена!\n\n"
            f"💰 Твой баланс: {user[3]} G",
            reply_markup=keyboard
        )
    else:
        await callback.answer("❌ Вы еще не подписались на все каналы!", show_alert=True)


# ===== ОБЩИЕ КОМАНДЫ =====

# Профиль
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    if not await subscription_required(message):
        return

    user = db.get_user(message.from_user.id)

    text = (
        f"👤 <b>Твой профиль</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Имя: {message.from_user.first_name}\n"
        f"💰 Баланс: {user[3]} G\n"
        f"💵 Всего заработано: {user[4]} G\n"
        f"👥 Приглашено: {user[6]} чел."
    )

    await message.answer(text, parse_mode="HTML")


# Баланс
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    if not await subscription_required(message):
        return

    user = db.get_user(message.from_user.id)
    await message.answer(
        f"💰 <b>Твой баланс</b>\n\n"
        f"Доступно: {user[3]} G\n"
        f"Всего заработано: {user[4]} G",
        parse_mode="HTML"
    )


# Статистика
@dp.message(F.text == "📊 Статистика")
async def show_my_stats(message: types.Message):
    if not await subscription_required(message):
        return

    user = db.get_user(message.from_user.id)

    db.cursor.execute('SELECT COUNT(*) FROM completed_tasks WHERE user_id = ?', (message.from_user.id,))
    tasks_completed = db.cursor.fetchone()[0]

    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"💰 Текущий баланс: {user[3]} G\n"
        f"💵 Всего заработано: {user[4]} G\n"
        f"👥 Приглашено рефералов: {user[6]}\n"
        f"🎁 Выполнено заданий: {tasks_completed}\n"
        f"📅 В боте с: {user[7]}"
    )

    await message.answer(text, parse_mode="HTML")


# Рефералы
@dp.message(F.text == "👥 Рефералы")
async def show_referrals(message: types.Message):
    if not await subscription_required(message):
        return

    referrals = db.get_referrals(message.from_user.id)
    user = db.get_user(message.from_user.id)

    text = f"👥 <b>Твои рефералы</b>\n\n"
    text += f"Всего приглашено: {user[6]}\n"
    text += f"Заработано с рефералов: {user[6] * REFERRAL_BONUS} G\n\n"

    if referrals:
        text += "<b>Список рефералов:</b>\n"
        for i, ref in enumerate(referrals, 1):
            text += f"{i}. {ref[2]} (@{ref[1]}) - {ref[3]}\n"
    else:
        text += "У тебя пока нет рефералов. Приглашай друзей и получай бонусы!"

    await message.answer(text, parse_mode="HTML")


# Реферальная ссылка
@dp.message(F.text == "🔗 Реферальная ссылка")
async def show_ref_link(message: types.Message):
    if not await subscription_required(message):
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"

    await message.answer(
        f"🔗 <b>Твоя реферальная ссылка</b>\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"За каждого приглашенного друга ты получаешь {REFERRAL_BONUS} G!",
        parse_mode="HTML"
    )


# Каналы
@dp.message(F.text == "📢 Каналы")
async def show_channels(message: types.Message):
    if not await subscription_required(message):
        return

    channels = db.get_channels()

    if not channels:
        await message.answer("📢 Нет обязательных каналов для подписки.")
        return

    text = "📢 <b>Наши каналы</b>\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for channel in channels:
        text += f"• {channel[3]}\n"
        username = channel[2].replace('@', '')
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"📢 {channel[3]}", url=f"https://t.me/{username}")
        ])

    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


# Помощь
@dp.message(F.text == "❓ Помощь")
async def show_help(message: types.Message):
    if not await subscription_required(message):
        return

    text = (
        "❓ <b>Помощь по боту</b>\n\n"
        "👤 <b>Профиль</b> - информация о тебе\n"
        "💰 <b>Баланс</b> - текущий баланс\n"
        "📊 <b>Статистика</b> - твоя статистика\n"
        "👥 <b>Рефералы</b> - список приглашенных\n"
        "🎁 <b>Задания</b> - доступные задания\n"
        "💳 <b>Вывод</b> - вывод средств\n"
        "🔗 <b>Реферальная ссылка</b> - твоя ссылка для приглашений\n"
        "📢 <b>Каналы</b> - наши каналы\n\n"
        "📝 <b>Промокоды</b> - используй /promo КОД"
    )

    await message.answer(text, parse_mode="HTML")


# Задания
@dp.message(F.text == "🎁 Задания")
async def show_tasks(message: types.Message):
    if not await subscription_required(message):
        return

    tasks = db.get_user_tasks(message.from_user.id)

    if not tasks:
        await message.answer("🎁 Пока нет доступных заданий!")
        return

    text = "🎁 <b>Доступные задания</b>\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for task in tasks:
        status = "✅" if task[7] == 1 else "⏳"
        text += f"{status} <b>{task[1]}</b> - {task[3]} G\n"
        text += f"└ {task[2]}\n\n"

        if task[7] == 0:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"👉 {task[1]}",
                    callback_data=f"task_{task[0]}"
                )
            ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


# Выполнение задания
@dp.callback_query(F.data.startswith("task_"))
async def complete_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    task = db.get_task(task_id)

    if not task:
        await callback.answer("❌ Задание не найдено", show_alert=True)
        return

    if task[4] == "subscription":
        try:
            member = await bot.get_chat_member(chat_id=task[5], user_id=callback.from_user.id)
            if member.status != 'left':
                success, msg = db.complete_task(callback.from_user.id, task_id)
                await callback.answer(msg, show_alert=True)

                if success:
                    await callback.message.delete()
                    await show_tasks(callback.message)
            else:
                username = task[5].replace('@', '')
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{username}")],
                        [InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_task_{task_id}")]
                    ]
                )
                await callback.message.edit_text(
                    f"Для выполнения задания подпишись на канал:\n{task[5]}",
                    reply_markup=keyboard
                )
        except Exception as e:
            await callback.answer("❌ Ошибка проверки подписки", show_alert=True)


@dp.callback_query(F.data.startswith("check_task_"))
async def check_task_callback(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])

    task = db.get_task(task_id)

    try:
        member = await bot.get_chat_member(chat_id=task[5], user_id=callback.from_user.id)
        if member.status != 'left':
            success, msg = db.complete_task(callback.from_user.id, task_id)
            await callback.answer(msg, show_alert=True)

            if success:
                await callback.message.delete()
                await show_tasks(callback.message)
        else:
            await callback.answer("❌ Вы еще не подписались!", show_alert=True)
    except:
        await callback.answer("❌ Ошибка проверки", show_alert=True)


# ===== ВЫВОД СРЕДСТВ =====
@dp.message(F.text == "💳 Вывод")
async def withdraw_start(message: types.Message, state: FSMContext):
    if not await subscription_required(message):
        return

    user = db.get_user(message.from_user.id)

    if user[3] < MIN_WITHDRAW:
        await message.answer(
            f"❌ Минимальная сумма вывода {MIN_WITHDRAW} G\n"
            f"Твой баланс: {user[3]} G"
        )
        return

    await state.set_state(WithdrawStates.waiting_for_amount)
    await message.answer(
        f"💰 Введите сумму для вывода (мин. {MIN_WITHDRAW} G):\n"
        f"Доступно: {user[3]} G",
        reply_markup=get_back_keyboard()
    )


@dp.message(WithdrawStates.waiting_for_amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Главное меню", reply_markup=get_main_keyboard())
        return

    try:
        amount = float(message.text)
        user = db.get_user(message.from_user.id)

        if amount < MIN_WITHDRAW:
            await message.answer(f"❌ Минимальная сумма {MIN_WITHDRAW} G")
            return

        if amount > user[3]:
            await message.answer("❌ Недостаточно средств")
            return

        await state.update_data(amount=amount)
        await state.set_state(WithdrawStates.waiting_for_wallet)
        await message.answer(
            "📝 Введите паттерн/скин для вывода:",
            reply_markup=get_back_keyboard()
        )

    except ValueError:
        await message.answer("❌ Введите корректное число")


@dp.message(WithdrawStates.waiting_for_wallet)
async def withdraw_wallet(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Главное меню", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    success, msg, withdrawal_id = db.create_withdraw_request(
        message.from_user.id,
        data['amount'],
        message.text
    )

    await state.clear()

    if message.from_user.id in ADMIN_IDS:
        keyboard = get_admin_main_keyboard()
    else:
        keyboard = get_main_keyboard()

    await message.answer(msg, reply_markup=keyboard)

    # Отправляем сигнал администратору о новой заявке
    if success:
        user = db.get_user(message.from_user.id)
        for admin_id in ADMIN_IDS:
            if admin_id != message.from_user.id:  # Не отправляем самому себе
                try:
                    # Создаем клавиатуру для быстрого ответа админу
                    admin_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✅ Подтвердить",
                                    callback_data=f"admin_confirm_{withdrawal_id}"
                                ),
                                InlineKeyboardButton(
                                    text="❌ Отклонить",
                                    callback_data=f"admin_reject_{withdrawal_id}"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="👤 Профиль пользователя",
                                    callback_data=f"admin_user_{message.from_user.id}"
                                )
                            ]
                        ]
                    )

                    await bot.send_message(
                        admin_id,
                        f"🚨 <b>НОВАЯ ЗАЯВКА НА ВЫВОД #{withdrawal_id}</b> 🚨\n\n"
                        f"👤 <b>Пользователь:</b> @{message.from_user.username or 'Нет юзернейма'}\n"
                        f"📝 <b>Имя:</b> {message.from_user.first_name}\n"
                        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
                        f"💰 <b>Сумма:</b> {data['amount']} G\n"
                        f"💳 <b>Кошелек:</b> <code>{message.text}</code>\n"
                        f"💵 <b>Баланс после вывода:</b> {user[3] - data['amount']} G\n"
                        f"📅 <b>Дата:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"Выберите действие:",
                        parse_mode="HTML",
                        reply_markup=admin_keyboard
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления админу: {e}")


# Обработчики для быстрых действий админа из уведомления
@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ У вас нет прав администратора")
        return

    withdrawal_id = int(callback.data.split("_")[2])
    withdrawal = db.complete_withdrawal(withdrawal_id)

    if withdrawal:
        # Уведомляем пользователя
        try:
            await bot.send_message(
                withdrawal[1],
                f"✅ Ваша заявка на вывод {withdrawal[2]} G подтверждена администратором!"
            )
        except:
            pass

        await callback.message.edit_text(
            f"✅ Заявка #{withdrawal_id} подтверждена!\n\n"
            f"Сумма {withdrawal[2]} G выплачена пользователю."
        )
        await callback.answer("✅ Заявка подтверждена")
    else:
        await callback.answer("❌ Заявка не найдена", show_alert=True)


@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ У вас нет прав администратора")
        return

    withdrawal_id = int(callback.data.split("_")[2])
    withdrawal = db.reject_withdrawal(withdrawal_id)

    if withdrawal:
        # Уведомляем пользователя
        try:
            await bot.send_message(
                withdrawal[1],
                f"❌ Ваша заявка на вывод {withdrawal[2]} G отклонена администратором.\n"
                f"Средства возвращены на баланс."
            )
        except:
            pass

        await callback.message.edit_text(
            f"❌ Заявка #{withdrawal_id} отклонена.\n\n"
            f"Средства возвращены пользователю."
        )
        await callback.answer("❌ Заявка отклонена")
    else:
        await callback.answer("❌ Заявка не найдена", show_alert=True)


@dp.callback_query(F.data.startswith("admin_user_"))
async def admin_show_user(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ У вас нет прав администратора")
        return

    user_id = int(callback.data.split("_")[2])
    user = db.get_user(user_id)

    if user:
        text = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"🆔 ID: <code>{user[0]}</code>\n"
            f"👤 Имя: {user[2]}\n"
            f"📝 Username: @{user[1]}\n"
            f"💰 Баланс: {user[3]} G\n"
            f"💵 Всего заработано: {user[4]} G\n"
            f"👥 Рефералов: {user[6]}\n"
            f"📅 В боте с: {user[7]}"
        )
        await callback.message.answer(text, parse_mode="HTML")
    else:
        await callback.answer("❌ Пользователь не найден", show_alert=True)


# Промокод
@dp.message(F.text.startswith("/promo"))
async def use_promocode(message: types.Message):
    if not await subscription_required(message):
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("📝 Использование: /promo КОД\nПример: /promo GOLD2024")
        return

    code = args[1].upper()
    success, msg = db.use_promocode(message.from_user.id, code)
    await message.answer(msg)


# ================== АДМИН-ПАНЕЛЬ ==================
@dp.message(F.text == "👑 Админ панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора")
        return

    await message.answer(
        "👑 <b>Админ-панель</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard()
    )


# ===== УПРАВЛЕНИЕ КАНАЛАМИ =====
@dp.message(F.text == "📢 Управление каналами")
async def manage_channels(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer(
        "📢 <b>Управление обязательными каналами</b>",
        parse_mode="HTML",
        reply_markup=get_channels_management_keyboard()
    )


@dp.message(F.text == "➕ Добавить канал")
async def add_channel_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_channel)
    await message.answer(
        "📝 Отправьте username канала (например @mychannel)\n"
        "Или перешлите любое сообщение из канала:",
        reply_markup=get_back_keyboard()
    )


@dp.message(AdminStates.waiting_for_channel)
async def add_channel_process(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление каналами", reply_markup=get_channels_management_keyboard())
        return

    channel_username = None
    channel_title = None

    if message.forward_from_chat:
        channel = message.forward_from_chat
        channel_username = channel.username
        channel_title = channel.title
    else:
        channel_username = message.text.strip()
        if not channel_username.startswith('@'):
            channel_username = '@' + channel_username

    if not channel_username:
        await message.answer("❌ Не удалось определить канал")
        return

    if db.channel_exists(channel_username):
        await message.answer("❌ Этот канал уже добавлен!")
        await state.clear()
        return

    try:
        username_for_chat = channel_username.replace('@', '')
        chat = await bot.get_chat(username_for_chat)
        channel_title = chat.title
        channel_id = chat.id

        db.add_channel(channel_id, channel_username, channel_title)

        await message.answer(
            f"✅ Канал успешно добавлен!\n\n"
            f"Название: {channel_title}\n"
            f"Username: {channel_username}",
            reply_markup=get_channels_management_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: Бот не является администратором канала")

    await state.clear()


@dp.message(F.text == "📋 Список каналов")
async def list_channels(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    channels = db.get_channels()

    if not channels:
        await message.answer("📋 Список обязательных каналов пуст.")
        return

    text = "📋 <b>Обязательные каналы:</b>\n\n"
    for channel in channels:
        text += f"• <b>{channel[3]}</b> - {channel[2]}\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "➖ Удалить канал")
async def remove_channel_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    channels = db.get_channels()

    if not channels:
        await message.answer("❌ Нет каналов для удаления.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for channel in channels:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"❌ {channel[3]}",
                callback_data=f"remove_channel_{channel[0]}"
            )
        ])

    await message.answer("Выберите канал для удаления:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("remove_channel_"))
async def remove_channel_callback(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет прав")
        return

    channel_db_id = int(callback.data.split("_")[2])

    db.cursor.execute('SELECT * FROM required_channels WHERE id = ?', (channel_db_id,))
    channel = db.cursor.fetchone()

    if channel:
        db.remove_channel(channel[1])
        await callback.message.edit_text(f"✅ Канал {channel[3]} удален!")
    else:
        await callback.answer("❌ Канал не найден", show_alert=True)


# ===== УПРАВЛЕНИЕ ПРОМОКОДАМИ =====
@dp.message(F.text == "🎫 Управление промокодами")
async def manage_promocodes(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer(
        "🎫 <b>Управление промокодами</b>",
        parse_mode="HTML",
        reply_markup=get_promocodes_management_keyboard()
    )


@dp.message(F.text == "➕ Создать промокод")
async def create_promocode_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_promocode)
    await message.answer(
        "Введите название промокода (например GOLD2024):",
        reply_markup=get_back_keyboard()
    )


@dp.message(AdminStates.waiting_for_promocode)
async def create_promocode_code(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocodes_management_keyboard())
        return

    code = message.text.upper().replace(' ', '')
    await state.update_data(code=code)
    await state.set_state(AdminStates.waiting_for_promocode_amount)
    await message.answer("Введите сумму награды (в G):")


@dp.message(AdminStates.waiting_for_promocode_amount)
async def create_promocode_amount(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocodes_management_keyboard())
        return

    try:
        amount = float(message.text)
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0")
            return
        await state.update_data(amount=amount)
        await state.set_state(AdminStates.waiting_for_promocode_uses)
        await message.answer("Введите максимальное количество использований:")
    except ValueError:
        await message.answer("❌ Введите корректное число")


@dp.message(AdminStates.waiting_for_promocode_uses)
async def create_promocode_uses(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocodes_management_keyboard())
        return

    try:
        uses = int(message.text)
        if uses <= 0:
            await message.answer("❌ Количество использований должно быть больше 0")
            return

        data = await state.get_data()
        db.create_promocode(data['code'], data['amount'], uses)

        await state.clear()
        await message.answer(
            f"✅ Промокод создан!\n\n"
            f"Код: {data['code']}\n"
            f"Награда: {data['amount']} G\n"
            f"Макс. использований: {uses}",
            reply_markup=get_promocodes_management_keyboard()
        )
    except ValueError:
        await message.answer("❌ Введите корректное число")


@dp.message(F.text == "📋 Список промокодов")
async def list_promocodes(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    promocodes = db.get_all_promocodes()

    if not promocodes:
        await message.answer("📋 Список промокодов пуст.")
        return

    text = "🎫 <b>Список промокодов:</b>\n\n"
    for promo in promocodes:
        text += f"• <b>{promo[0]}</b> - {promo[1]} G\n"
        text += f"  Использовано: {promo[3]}/{promo[2]}\n\n"

    await message.answer(text, parse_mode="HTML")


# ===== УПРАВЛЕНИЕ ЗАДАНИЯМИ =====
@dp.message(F.text == "📝 Управление заданиями")
async def manage_tasks(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer(
        "📝 <b>Управление заданиями</b>",
        parse_mode="HTML",
        reply_markup=get_tasks_management_keyboard()
    )


@dp.message(F.text == "➕ Создать задание")
async def create_task_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_task_name)
    await message.answer(
        "Введите название задания:",
        reply_markup=get_back_keyboard()
    )


@dp.message(AdminStates.waiting_for_task_name)
async def create_task_name(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return

    await state.update_data(name=message.text)
    await state.set_state(AdminStates.waiting_for_task_desc)
    await message.answer("Введите описание задания:")


@dp.message(AdminStates.waiting_for_task_desc)
async def create_task_desc(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return

    await state.update_data(desc=message.text)
    await state.set_state(AdminStates.waiting_for_task_reward)
    await message.answer("Введите награду за задание (в G):")


@dp.message(AdminStates.waiting_for_task_reward)
async def create_task_reward(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return

    try:
        reward = float(message.text)
        if reward <= 0:
            await message.answer("❌ Награда должна быть больше 0")
            return
        await state.update_data(reward=reward)
        await state.set_state(AdminStates.waiting_for_task_target)
        await message.answer(
            "Введите username канала для подписки (например @channel):"
        )
    except ValueError:
        await message.answer("❌ Введите корректное число")


@dp.message(AdminStates.waiting_for_task_target)
async def create_task_target(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return

    data = await state.get_data()
    target = message.text
    if not target.startswith('@'):
        target = '@' + target

    db.add_task(data['name'], data['desc'], data['reward'], "subscription", target)

    await state.clear()
    await message.answer(
        f"✅ Задание создано!\n\n"
        f"Название: {data['name']}\n"
        f"Награда: {data['reward']} G\n"
        f"Канал: {target}",
        reply_markup=get_tasks_management_keyboard()
    )


@dp.message(F.text == "📋 Список заданий")
async def list_tasks(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    tasks = db.get_tasks()

    if not tasks:
        await message.answer("📋 Список заданий пуст.")
        return

    text = "📋 <b>Список заданий:</b>\n\n"
    for task in tasks:
        text += f"• <b>{task[1]}</b> - {task[3]} G\n"
        text += f"  {task[2]}\n"
        text += f"  Канал: {task[5]}\n\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "➖ Удалить задание")
async def delete_task_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    tasks = db.get_tasks()

    if not tasks:
        await message.answer("❌ Нет заданий для удаления.")
        return

    await state.set_state(AdminStates.waiting_for_task_delete)
    await message.answer(
        "Введите ID задания для удаления (можно посмотреть в списке заданий):",
        reply_markup=get_back_keyboard()
    )


@dp.message(AdminStates.waiting_for_task_delete)
async def delete_task_process(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в меню":
        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
        return

    try:
        task_id = int(message.text)
        task = db.get_task(task_id)

        if task:
            db.delete_task(task_id)
            await message.answer(f"✅ Задание '{task[1]}' удалено!")
        else:
            await message.answer("❌ Задание с таким ID не найдено")

        await state.clear()
        await message.answer("Управление заданиями", reply_markup=get_tasks_management_keyboard())
    except ValueError:
        await message.answer("❌ Введите корректный ID")


# ===== ЗАЯВКИ НА ВЫВОД =====
@dp.message(F.text == "📋 Заявки на вывод")
async def show_withdrawals(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    withdrawals = db.get_pending_withdrawals()

    if not withdrawals:
        await message.answer("📋 Нет активных заявок на вывод")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for w in withdrawals:
        user = db.get_user(w[1])
        username = user[1] if user else "Unknown"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"💰 {w[2]} G - @{username}",
                callback_data=f"withdraw_{w[0]}"
            )
        ])

    await message.answer("📋 <b>Заявки на вывод:</b>", parse_mode="HTML", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("withdraw_"))
async def process_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет прав")
        return

    withdrawal_id = int(callback.data.split("_")[1])
    withdrawal = db.get_withdrawal(withdrawal_id)

    if not withdrawal:
        await callback.answer("❌ Заявка не найдена")
        return

    user = db.get_user(withdrawal[1])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_withdraw_{withdrawal_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_withdraw_{withdrawal_id}")
            ]
        ]
    )

    await callback.message.edit_text(
        f"📬 <b>Заявка на вывод #{withdrawal_id}</b>\n\n"
        f"👤 Пользователь: @{user[1]}\n"
        f"💰 Сумма: {withdrawal[2]} G\n"
        f"💳 Кошелек: <code>{withdrawal[3]}</code>\n"
        f"📅 Дата: {withdrawal[5]}",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("confirm_withdraw_"))
async def confirm_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    withdrawal_id = int(callback.data.split("_")[2])
    withdrawal = db.complete_withdrawal(withdrawal_id)

    if withdrawal:
        try:
            await bot.send_message(
                withdrawal[1],
                f"✅ Ваша заявка на вывод {withdrawal[2]} G подтверждена!"
            )
        except:
            pass

        await callback.message.edit_text(f"✅ Заявка #{withdrawal_id} подтверждена!")
        await callback.answer("✅ Заявка подтверждена")
    else:
        await callback.answer("❌ Заявка не найдена", show_alert=True)


@dp.callback_query(F.data.startswith("reject_withdraw_"))
async def reject_withdrawal(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    withdrawal_id = int(callback.data.split("_")[2])
    withdrawal = db.reject_withdrawal(withdrawal_id)

    if withdrawal:
        try:
            await bot.send_message(
                withdrawal[1],
                f"❌ Ваша заявка на вывод {withdrawal[2]} G отклонена.\n"
                f"Средства возвращены на баланс."
            )
        except:
            pass

        await callback.message.edit_text(f"❌ Заявка #{withdrawal_id} отклонена")
        await callback.answer("❌ Заявка отклонена")
    else:
        await callback.answer("❌ Заявка не найдена", show_alert=True)


# ===== СТАТИСТИКА БОТА =====
@dp.message(F.text == "📊 Статистика бота")
async def bot_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    total_users = db.get_total_users()
    total_balance = db.get_total_balance()
    channels = db.get_channels()
    pending_withdrawals = len(db.get_pending_withdrawals())
    total_tasks = len(db.get_tasks())
    completed_tasks = db.get_total_tasks_completed()

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"💰 Общий баланс: {total_balance} G\n"
        f"📢 Обязательных каналов: {len(channels)}\n"
        f"🎁 Всего заданий: {total_tasks}\n"
        f"✅ Выполнено заданий: {completed_tasks}\n"
        f"⏳ Ожидают вывода: {pending_withdrawals}"
    )

    await message.answer(text, parse_mode="HTML")


# ===== НАВИГАЦИЯ =====
@dp.message(F.text == "🔙 Назад в меню")
async def back_to_menu(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Главное меню", reply_markup=get_admin_main_keyboard())
    else:
        await message.answer("Главное меню", reply_markup=get_main_keyboard())


@dp.message(F.text == "🔙 Назад в админку")
async def back_to_admin(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Админ-панель", reply_markup=get_admin_panel_keyboard())


# ================== ЗАПУСК БОТА ==================
async def main():
    logging.info("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())