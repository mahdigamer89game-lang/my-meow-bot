import asyncio
import logging
import os
import random
import string
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import psycopg2
from psycopg2 import pool

TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = 1713081033
CARD_NUMBER = "5047061673513814"
CARD_NAME = "صادقی"
REFERRAL_REWARD = 150000
MIN_WITHDRAW = 500000
MIN_REFERRALS = 5
USERS_PER_PAGE = 10

REQUIRED_CHANNELS = [
    {"id": "@Channle_Meowie_Buy", "link": "https://t.me/Channle_Meowie_Buy"},
    {"id": "@Meow_Point_Free", "link": "https://t.me/Meow_Point_Free"},
]

logging.basicConfig(level=logging.INFO)
connection_pool = psycopg2.pool.SimpleConnectionPool(1, 20, dsn=DATABASE_URL)

def db_execute(query, params=None, fetch=False):
    conn = connection_pool.getconn()
    try:
        c = conn.cursor()
        c.execute(query, params)
        result = c.fetchone() if fetch else None
        conn.commit()
        c.close()
        return result
    except Exception as e:
        conn.rollback()
        logging.error(f"DB Error: {e}")
        raise
    finally:
        connection_pool.putconn(conn)

def db_execute_all(query, params=None):
    conn = connection_pool.getconn()
    try:
        c = conn.cursor()
        c.execute(query, params)
        result = c.fetchall()
        c.close()
        return result
    except Exception as e:
        logging.error(f"DB Error: {e}")
        raise
    finally:
        connection_pool.putconn(conn)

def init_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            wallet BIGINT DEFAULT 0,
            ref_code VARCHAR(10) UNIQUE,
            referred_by BIGINT,
            referrals_count INTEGER DEFAULT 0,
            total_reward BIGINT DEFAULT 0,
            full_name TEXT,
            username TEXT,
            joined_at TIMESTAMP DEFAULT NOW(),
            notes TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS pending_charges (
            id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, photo_id TEXT, card_photo_id TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""",
        """CREATE TABLE IF NOT EXISTS withdraw_requests (
            id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, card TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, type TEXT, description TEXT, created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS pending_refs (user_id BIGINT PRIMARY KEY, ref_code VARCHAR(20))"""
    ]
    for q in queries:
        try: db_execute(q)
        except Exception as e: logging.error(f"Init DB error: {e}")

init_db()

def get_setting(key, default):
    r = db_execute("SELECT value FROM settings WHERE key=%s", (key,), fetch=True)
    if r is None:
        db_execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, str(default)))
        return default
    return int(r[0])

def set_setting(key, value):
    db_execute("""INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, str(value)))

def get_price_high(): return get_setting("price_high", 2100)
def get_price_low(): return get_setting("price_low", 2500)
def get_threshold(): return get_setting("threshold", 100)

def gen_ref_code():
    while True:
        code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        r = db_execute("SELECT 1 FROM users WHERE ref_code=%s", (code,), fetch=True)
        if not r: return code

def get_wallet(uid):
    r = db_execute("SELECT wallet FROM users WHERE user_id=%s", (uid,), fetch=True)
    if r is None:
        db_execute("INSERT INTO users (user_id, wallet, ref_code) VALUES (%s, 0, %s)", (uid, gen_ref_code()))
        return 0
    return r[0]

def add_wallet(uid, delta, description=""):
    new = get_wallet(uid) + delta
    db_execute("""INSERT INTO users (user_id, wallet) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET wallet = EXCLUDED.wallet""", (uid, new))
    if description:
        db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)", (uid, delta, "add" if delta > 0 else "sub", description))
    return new

def add_transaction(uid, amount, ttype, desc):
    db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)", (uid, amount, ttype, desc))

def get_user(uid):
    return db_execute("SELECT user_id, wallet, ref_code, referred_by, referrals_count, total_reward, full_name, username, joined_at, notes FROM users WHERE user_id=%s", (uid,), fetch=True)

def get_user_by_ref(code):
    return db_execute("SELECT user_id FROM users WHERE ref_code=%s", (code,), fetch=True)

def user_exists(uid):
    r = db_execute("SELECT 1 FROM users WHERE user_id=%s", (uid,), fetch=True)
    return r is not None

def get_all_users():
    rows = db_execute_all("SELECT user_id FROM users ORDER BY joined_at DESC")
    return [r[0] for r in rows]

def get_users_page(page=0):
    offset = page * USERS_PER_PAGE
    return db_execute_all("SELECT user_id, full_name, username, wallet FROM users ORDER BY joined_at DESC LIMIT %s OFFSET %s", (USERS_PER_PAGE, offset))

def get_users_count():
    r = db_execute("SELECT COUNT(*) FROM users", fetch=True)
    return r[0] if r else 0

def search_users(query):
    q = f"%{query}%"
    return db_execute_all("""SELECT user_id, full_name, username, wallet FROM users WHERE full_name ILIKE %s OR username ILIKE %s OR CAST(user_id AS TEXT) LIKE %s ORDER BY joined_at DESC LIMIT 20""", (q, q, q))

def fa_to_en(text):
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def set_state(uid, state, data=""):
    db_execute("""INSERT INTO states (user_id, state, data) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data""", (uid, state, data))

def get_state(uid):
    r = db_execute("SELECT state, data FROM states WHERE user_id=%s", (uid,), fetch=True)
    return (r[0], r[1]) if r else (None, "")

def clear_state(uid):
    db_execute("DELETE FROM states WHERE user_id=%s", (uid,))

def is_admin(uid):
    return uid == ADMIN_ID

async def check_membership(context, uid):
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch["id"], uid)
            if member.status in ["left", "kicked"]: not_joined.append(ch)
        except: not_joined.append(ch)
    return not_joined
    def main_kb(uid=None):
    rows = [
        [KeyboardButton("🛒 خرید میو پوینت")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("👥 دعوت دوستان"), KeyboardButton("🤝 چطور اعتماد کنم")],
        [KeyboardButton("📊 موجودی ما چقدره"), KeyboardButton("📖 راهنما")],
    ]
    if uid and is_admin(uid):
        rows.append([KeyboardButton("🔐 پنل ادمین")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def wallet_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("💳 شارژ کیف پول")], [KeyboardButton("🔙 بازگشت")]], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📢 پیام همگانی")], [KeyboardButton("👥 لیست کاربران")],
        [KeyboardButton("💵 قیمت بالای 100"), KeyboardButton("💵 قیمت پایین 100")],
        [KeyboardButton("💰 شارژ همه کاربران"), KeyboardButton("🎁 جایزه همه کاربران")],
        [KeyboardButton("💸 کسر جایزه از همه کاربران")], [KeyboardButton("🔙 بازگشت")]
    ], resize_keyboard=True)

def user_manage_kb(target_uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 شارژ کیف پول", callback_data=f"um_charge_{target_uid}")],
        [InlineKeyboardButton("💸 کسر از کیف پول", callback_data=f"um_deduct_{target_uid}")],
        [InlineKeyboardButton("👥 افزودن رفرال", callback_data=f"um_ref_{target_uid}")],
        [InlineKeyboardButton("📊 اطلاعات کامل", callback_data=f"um_info_{target_uid}")],
        [InlineKeyboardButton("📢 ارسال پیام", callback_data=f"um_msg_{target_uid}")],
        [InlineKeyboardButton("📜 سفارش‌ها", callback_data=f"um_orders_{target_uid}")],
        [InlineKeyboardButton("🎁 افزودن جایزه دعوت", callback_data=f"um_bonus_{target_uid}")],
        [InlineKeyboardButton("📝 یادداشت شخصی", callback_data=f"um_note_{target_uid}")],
        [InlineKeyboardButton("📊 تاریخچه تراکنش‌ها", callback_data=f"um_history_{target_uid}")],
        [InlineKeyboardButton("🗑 حذف کاربر", callback_data=f"um_delete_{target_uid}")],
    ])

def cancel_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True)

def join_kb(not_joined):
    buttons = [[InlineKeyboardButton(f"📢 عضویت در {ch['id']}", url=ch["link"])] for ch in not_joined]
    buttons.append([InlineKeyboardButton("✅ تلاش مجدد", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

async def process_start_logic(update, context, uid, user):
    is_new = not user_exists(uid)
    pending = db_execute("SELECT ref_code FROM pending_refs WHERE user_id=%s", (uid,), fetch=True)
    if is_new:
        db_execute("INSERT INTO users (user_id, wallet, ref_code, full_name, username) VALUES (%s, 0, %s, %s, %s)", (uid, gen_ref_code(), user.full_name, user.username or ""))
    else:
        db_execute("UPDATE users SET full_name=%s, username=%s WHERE user_id=%s", (user.full_name, user.username or "", uid))
    if pending:
        ref_code = pending[0]
        ref_owner = get_user_by_ref(ref_code)
        if ref_owner:
            ref_uid = ref_owner[0]
            if ref_uid != uid:
                already = db_execute("SELECT referred_by FROM users WHERE user_id=%s", (uid,), fetch=True)
                if already and already[0] is None:
                    db_execute("UPDATE users SET referred_by=%s WHERE user_id=%s", (ref_uid, uid))
                    db_execute("UPDATE users SET referrals_count=referrals_count+1, total_reward=total_reward+%s WHERE user_id=%s", (REFERRAL_REWARD, ref_uid))
                    add_transaction(ref_uid, REFERRAL_REWARD, "referral", f"دعوت کاربر {user.full_name}")
                    try: await context.bot.send_message(ref_uid, f"👤 کاربر {user.full_name} با لینک شما وارد شد!\n🎁 {REFERRAL_REWARD:,} میوپوینت به جایزه‌های شما اضافه شد.")
                    except: pass
        db_execute("DELETE FROM pending_refs WHERE user_id=%s", (uid,))
    clear_state(uid)
    await context.bot.send_message(uid, "✅ عضویت شما تایید شد. با تشکر!\n\nلطفا یک گزینه را انتخاب کنید:", reply_markup=main_kb(uid))

async def start(update, context):
    uid = update.effective_user.id
    args = context.args
    user = update.effective_user
    if args and not user_exists(uid):
        ref_code = args[0]
        db_execute("INSERT INTO pending_refs (user_id, ref_code) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET ref_code = EXCLUDED.ref_code", (uid, ref_code))
    not_joined = await check_membership(context, uid)
    if not_joined:
        text = "⚠️ برای استفاده از ربات، ابتدا باید در کانال‌های زیر عضو شوید:\n\n"
        for ch in not_joined: text += f"📢 {ch['id']}\n"
        text += "\nپس از عضویت، روی دکمه «✅ تلاش مجدد» بزنید."
        await update.message.reply_text(text, reply_markup=join_kb(not_joined))
        return
    await process_start_logic(update, context, uid, user)
    async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    not_joined = await check_membership(context, uid)
    if not_joined:
        txt = "⚠️ برای استفاده از ربات، ابتدا باید در کانال‌های زیر عضو شوید:\n\n"
        for ch in not_joined: txt += f"📢 {ch['id']}\n"
        txt += "\nپس از عضویت، روی دکمه «✅ تلاش مجدد» بزنید."
        await update.message.reply_text(txt, reply_markup=join_kb(not_joined))
        return
    state, data = get_state(uid)
    if state == "ADMIN_REPLY": return await do_send_reply(update, context, uid, data)
    if state == "WALLET_AMOUNT": return await do_wallet_amount(update, context, uid)
    if state ==
