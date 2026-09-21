import asyncio
import logging
import os
import random
import string
from datetime import datetime
from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import psycopg2
from psycopg2 import pool

TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = 1713081033
BOT_USERNAME = "Meowie_buybot"
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
connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

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
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP DEFAULT NOW()""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''""",
        """CREATE TABLE IF NOT EXISTS pending_charges (
            id SERIAL PRIMARY KEY,
            user_id BIGINT, amount BIGINT, photo_id TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""",
        """CREATE TABLE IF NOT EXISTS withdraw_requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT, amount BIGINT, card TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT, amount BIGINT, type TEXT, description TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS pending_refs (
            user_id BIGINT PRIMARY KEY,
            ref_code VARCHAR(20)
        )"""
    ]
    for q in queries:
        try:
            db_execute(q)
        except Exception as e:
            logging.error(f"Init DB error: {e}")

init_db()

def get_setting(key, default):
    r = db_execute("SELECT value FROM settings WHERE key=%s", (key,), fetch=True)
    if r is None:
        db_execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, str(default)))
        return default
    return int(r[0])

def set_setting(key, value):
    db_execute("""INSERT INTO settings (key, value) VALUES (%s, %s)
                  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (key, str(value)))

def get_price_high(): return get_setting("price_high", 2100)
def get_price_low(): return get_setting("price_low", 2500)
def get_threshold(): return get_setting("threshold", 100)

def gen_ref_code():
    while True:
        code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        r = db_execute("SELECT 1 FROM users WHERE ref_code=%s", (code,), fetch=True)
        if not r:
            return code

def get_wallet(uid):
    r = db_execute("SELECT wallet FROM users WHERE user_id=%s", (uid,), fetch=True)
    if r is None:
        db_execute("INSERT INTO users (user_id, wallet, ref_code) VALUES (%s, 0, %s)", (uid, gen_ref_code()))
        return 0
    return r[0]

def add_wallet(uid, delta, description=""):
    new = get_wallet(uid) + delta
    db_execute("""INSERT INTO users (user_id, wallet) VALUES (%s, %s)
                  ON CONFLICT (user_id) DO UPDATE SET wallet = EXCLUDED.wallet""", (uid, new))
    if description:
        db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                   (uid, delta, "add" if delta > 0 else "sub", description))
    return new

def add_transaction(uid, amount, ttype, desc):
    db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
               (uid, amount, ttype, desc))

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
    return db_execute_all("SELECT user_id, full_name, username, wallet FROM users ORDER BY joined_at DESC LIMIT %s OFFSET %s",
                          (USERS_PER_PAGE, offset))

def get_users_count():
    r = db_execute("SELECT COUNT(*) FROM users", fetch=True)
    return r[0] if r else 0

def search_users(query):
    q = f"%{query}%"
    return db_execute_all("""SELECT user_id, full_name, username, wallet FROM users
                             WHERE full_name ILIKE %s OR username ILIKE %s OR CAST(user_id AS TEXT) LIKE %s
                             ORDER BY joined_at DESC LIMIT 20""", (q, q, q))

def fa_to_en(text):
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def set_state(uid, state, data=""):
    db_execute("""INSERT INTO states (user_id, state, data) VALUES (%s, %s, %s)
                  ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data""", (uid, state, data))

def get_state(uid):
    r = db_execute("SELECT state, data FROM states WHERE user_id=%s", (uid,), fetch=True)
    if r is None:
        return None, ""
    return r[0], r[1]

def clear_state(uid):
    db_execute("DELETE FROM states WHERE user_id=%s", (uid,))

def is_admin(uid):
    return uid == ADMIN_ID

async def check_membership(context, uid):
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(ch["id"], uid)
            if member.status in ["left", "kicked"]:
                not_joined.append(ch)
        except Exception as e:
            logging.error(f"Check membership error: {e}")
            not_joined.append(ch)
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
    return ReplyKeyboardMarkup([
        [KeyboardButton("💳 شارژ کیف پول")],
        [KeyboardButton("🔙 بازگشت")]
    ], resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📢 پیام همگانی")],
        [KeyboardButton("👥 لیست کاربران")],
        [KeyboardButton("💵 قیمت بالای 100"), KeyboardButton("💵 قیمت پایین 100")],
        [KeyboardButton("💰 شارژ همه کاربران"), KeyboardButton("🎁 جایزه همه کاربران")],
        [KeyboardButton("💸 کسر جایزه از همه کاربران")],
        [KeyboardButton("🔙 بازگشت")]
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
    buttons = []
    for ch in not_joined:
        buttons.append([InlineKeyboardButton(f"📢 عضویت در {ch['id']}", url=ch["link"])])
    buttons.append([InlineKeyboardButton("✅ تلاش مجدد", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

async def process_start_logic(update, context, uid, user):
    is_new = not user_exists(uid)
    pending = db_execute("SELECT ref_code FROM pending_refs WHERE user_id=%s", (uid,), fetch=True)

    if is_new:
        db_execute("INSERT INTO users (user_id, wallet, ref_code, full_name, username) VALUES (%s, 0, %s, %s, %s)",
                   (uid, gen_ref_code(), user.full_name, user.username or ""))
    else:
        db_execute("UPDATE users SET full_name=%s, username=%s WHERE user_id=%s",
                   (user.full_name, user.username or "", uid))

    if pending:
        ref_code = pending[0]
        ref_owner = get_user_by_ref(ref_code)
        if ref_owner:
            ref_uid = ref_owner[0]
            if ref_uid != uid:
                already = db_execute("SELECT referred_by FROM users WHERE user_id=%s", (uid,), fetch=True)
                if already and already[0] is None:
                    db_execute("UPDATE users SET referred_by=%s WHERE user_id=%s", (ref_uid, uid))
                    db_execute("UPDATE users SET referrals_count=referrals_count+1, total_reward=total_reward+%s WHERE user_id=%s",
                              (REFERRAL_REWARD, ref_uid))
                    add_transaction(ref_uid, REFERRAL_REWARD, "referral", f"دعوت کاربر {user.full_name}")
                    try:
                        await context.bot.send_message(
                            ref_uid,
                            f"👤 کاربر {user.full_name} با لینک شما وارد شد!\n"
                            f"🎁 {REFERRAL_REWARD:,} میوپوینت به جایزه‌های شما اضافه شد."
                        )
                    except Exception as e:
                        logging.error(e)
        db_execute("DELETE FROM pending_refs WHERE user_id=%s", (uid,))

    clear_state(uid)
    await context.bot.send_message(
        uid,
        "✅ عضویت شما تایید شد. با تشکر!\n\nلطفا یک گزینه را انتخاب کنید:",
        reply_markup=main_kb(uid)
    )

async def start(update, context):
    uid = update.effective_user.id
    args = context.args
    user = update.effective_user

    if args and not user_exists(uid):
        ref_code = args[0]
        db_execute("""INSERT INTO pending_refs (user_id, ref_code) VALUES (%s, %s)
                      ON CONFLICT (user_id) DO UPDATE SET ref_code = EXCLUDED.ref_code""", (uid, ref_code))

    not_joined = await check_membership(context, uid)
    if not_joined:
        text = "⚠️ برای استفاده از ربات، ابتدا باید در کانال‌های زیر عضو شوید:\n\n"
        for ch in not_joined:
            text += f"📢 {ch['id']}\n"
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
        for ch in not_joined:
            txt += f"📢 {ch['id']}\n"
        txt += "\nپس از عضویت، روی دکمه «✅ تلاش مجدد» بزنید."
        await update.message.reply_text(txt, reply_markup=join_kb(not_joined))
        return

    state, data = get_state(uid)

    if state == "ADMIN_REPLY":
        return await do_send_reply(update, context, uid, data)
    if state == "WALLET_AMOUNT":
        return await do_wallet_amount(update, context, uid)
    if state == "ADMIN_BROADCAST":
        return await do_broadcast(update, context, uid)
    if state == "ADMIN_SET_PRICE_HIGH":
        return await do_set_price(update, context, uid, "price_high")
    if state == "ADMIN_SET_PRICE_LOW":
        return await do_set_price(update, context, uid, "price_low")
    if state == "SUPPORT_MESSAGE":
        return await do_support_message(update, context, uid)
    if state == "BUY_AMOUNT":
        return await do_buy_amount(update, context, uid, data)
    if state == "BUY_CARD":
        return await do_buy_card(update, context, uid, data)
    if state == "WITHDRAW_AMOUNT":
        return await do_withdraw_amount(update, context, uid)
    if state == "WITHDRAW_CARD":
        return await do_withdraw_card(update, context, uid, data)
    if state == "ADMIN_SEARCH":
        return await do_search_user(update, context, uid)
    if state == "ADMIN_MSG_USER":
        return await do_msg_user(update, context, uid, data)
    if state == "ADMIN_CHARGE_USER":
        return await do_charge_user(update, context, uid, data)
    if state == "ADMIN_DEDUCT_USER":
        return await do_deduct_user(update, context, uid, data)
    if state == "ADMIN_BONUS_USER":
        return await do_bonus_user(update, context, uid, data)
    if state == "ADMIN_NOTE_USER":
        return await do_note_user(update, context, uid, data)
    if state == "ADMIN_REF_USER":
        return await do_add_ref_user(update, context, uid, data)
    if state == "ADMIN_CHARGE_ALL":
        return await do_charge_all(update, context, uid)
    if state == "ADMIN_BONUS_ALL":
        return await do_bonus_all(update, context, uid)
    if state == "ADMIN_DEDUCT_BONUS_ALL":
        return await do_deduct_bonus_all(update, context, uid)

    if text == "🛒 خرید میو پوینت":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("بالای 100 میلیون", callback_data="cat_high")],
            [InlineKeyboardButton("پایین 100 میلیون", callback_data="cat_low")]
        ])
        await update.message.reply_text("لطفا یک دسته را انتخاب کنید:", reply_markup=kb)
    elif text == "💰 کیف پول":
        bal = get_wallet(uid)
        await update.message.reply_text(f"💰 موجودی کیف پول شما: {bal:,} میوپوینت", reply_markup=wallet_kb())
    elif text == "📞 پشتیبانی":
        set_state(uid, "SUPPORT_MESSAGE")
        await update.message.reply_text("شما در حال تیکت به پشتیبانی هستید\nپیام خود را ارسال کنید:", reply_markup=cancel_kb())
    elif text == "👥 دعوت دوستان":
        get_wallet(uid)
        user = get_user(uid)
        ref_code = user[2]
        ref_count = user[4]
        total_reward = user[5]
        link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 دریافت جایزه", callback_data="withdraw")]
        ])
        await update.message.reply_text(
            f"👥 دعوت دوستان\n\n"
            f"🔗 کد اختصاصی شما:\n`{link}`\n\n"
            f"📊 تعداد دعوت‌ها: {ref_count}\n"
            f"🎁 جایزه هر دعوت: {REFERRAL_REWARD:,} میوپوینت\n"
            f"💰 مجموع جایزه‌ها: {total_reward:,} میوپوینت\n"
            f"⚠️ حداقل دریافت: {MIN_WITHDRAW:,} میوپوینت\n"
            f"⚠️ حداقل دعوت: {MIN_REFERRALS}\n\n"
            f"برای دریافت جایزه از دکمه زیر استفاده کنید:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    elif text == "🤝 چطور اعتماد کنم":
        await update.message.reply_text(
            "ما یک چنل داریم که در آن رضایت‌ها گذاشته می‌شوند\n"
            "https://t.me/Meow_Point_Free\n\n"
            "برای دیدن رضایت‌ها در چنل سرچ کنید #رضایت\n"
            "و اگر سوالی داشتید در پی وی بگید @Goooorba1234",
            reply_markup=main_kb(uid)
        )
    elif text == "📊 موجودی ما چقدره":
        await update.message.reply_text(
            "📊 موجودی ما چقدره؟\n\n"
            "موجودی ما تقریبا نامحدوده و هرچقدر بخواید می‌تونیم براتون جور کنیم.\n"
            "اما اگه مقداری که بخواید خیلی بالا باشه، یه کم طول می‌کشه تا براتون بزنیم.",
            reply_markup=main_kb(uid)
        )
    elif text == "📖 راهنما":
        await update.message.reply_text(
            "راهنمای خرید میوپوینت\n\n"
            "اول باید کیف پول را شارژ کنید\n"
            "وارد بخش کیف پول شوید و آن را شارژ کنید\n"
            "بعد از شارژ کردن به بخش خرید میو پوینت بروید\n\n"
            "اگر سوالی داشتید پی وی بگید @Goooorba1234",
            reply_markup=main_kb(uid)
        )
    elif text == "🔐 پنل ادمین" and is_admin(uid):
        await update.message.reply_text("🔐 پنل ادمین\n\nلطفا یک گزینه را انتخاب کنید:", reply_markup=admin_kb())
    elif text == "👥 لیست کاربران" and is_admin(uid):
        await show_users_page(update, context, 0)
    elif text == "💰 شارژ همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_CHARGE_ALL")
        await update.message.reply_text("💰 مقدار شارژ برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "🎁 جایزه همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_BONUS_ALL")
        await update.message.reply_text("🎁 مقدار جایزه رفرال برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "💸 کسر جایزه از همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_DEDUCT_BONUS_ALL")
        await update.message.reply_text("💸 مقدار کسر جایزه برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "💳 شارژ کیف پول":
        set_state(uid, "WALLET_AMOUNT")
        await update.message.reply_text(
            f"شما در حال کارت به کارت هستید\n"
            f"شماره کارت: {CARD_NUMBER}\n"
            f"بنام {CARD_NAME}\n\n"
            f"لطفا مبلغ پرداخت را وارد کنید:"
        )
    elif text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    elif text == "📢 پیام همگانی" and is_admin(uid):
        set_state(uid, "ADMIN_BROADCAST")
        await update.message.reply_text("متن پیام همگانی را وارد کنید:", reply_markup=cancel_kb())
    elif text == "💵 قیمت بالای 100" and is_admin(uid):
        set_state(uid, "ADMIN_SET_PRICE_HIGH")
        await update.message.reply_text(f"قیمت فعلی بالای 100: {get_price_high():,} تومان\n\nقیمت جدید:", reply_markup=cancel_kb())
    elif text == "💵 قیمت پایین 100" and is_admin(uid):
        set_state(uid, "ADMIN_SET_PRICE_LOW")
        await update.message.reply_text(f"قیمت فعلی پایین 100: {get_price_low():,} تومان\n\nقیمت جدید:", reply_markup=cancel_kb())
    else:
        await update.message.reply_text("لطفا از دکمه‌های زیر استفاده کنید:", reply_markup=main_kb(uid))

async def show_users_page(update, context, page):
    total = get_users_count()
    users = get_users_page(page)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    text = f"👥 لیست کاربران ({total} کاربر)\nصفحه {page+1} از {total_pages}\n\n"
    buttons = []
    for u in users:
        uid_u, full_name, username, wallet = u
        if full_name and full_name.strip():
            name = full_name
        else:
            name = f"کاربر {uid_u}"
        buttons.append([InlineKeyboardButton(f"{name} - {wallet:,}", callback_data=f"um_view_{uid_u}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"users_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"users_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔍 سرچ", callback_data="users_search")])
    kb = InlineKeyboardMarkup(buttons)
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)

async def do_search_user(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    query = update.message.text.strip()
    results = search_users(query)
    clear_state(uid)
    if not results:
        await update.message.reply_text("❌ کاربری پیدا نشد.", reply_markup=admin_kb())
        return
    text = f"🔍 نتایج سرچ ({len(results)} کاربر):\n\n"
    buttons = []
    for u in results:
        uid_u, full_name, username, wallet = u
        name = full_name if full_name and full_name.strip() else f"کاربر {uid_u}"
        buttons.append([InlineKeyboardButton(f"{name} - {wallet:,}", callback_data=f"um_view_{uid_u}")])
    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(text, reply_markup=kb)
    await update.message.reply_text("برای بازگشت:", reply_markup=admin_kb())

async def do_msg_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    text = update.message.text
    try:
        await context.bot.send_message(target_uid, f"📢 پیام از ادمین:\n\n{text}", reply_markup=main_kb(target_uid))
        await update.message.reply_text(f"✅ پیام به کاربر {target_uid} ارسال شد.", reply_markup=admin_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}", reply_markup=admin_kb())
    clear_state(uid)

async def do_charge_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    new_bal = add_wallet(target_uid, amount, f"شارژ دستی توسط ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} به کاربر {target_uid} اضافه شد.\nموجودی: {new_bal:,}", reply_markup=admin_kb())
    try:
        await context.bot.send_message(target_uid, f"🎁 {amount:,} میوپوینت به کیف پول شما اضافه شد!\nموجودی جدید: {new_bal:,}", reply_markup=main_kb(target_uid))
    except Exception:
        pass

async def do_deduct_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    new_bal = add_wallet(target_uid, -amount, f"کسر دستی توسط ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} از کاربر {target_uid} کسر شد.\nموجودی: {new_bal:,}", reply_markup=admin_kb())
    try:
        await context.bot.send_message(target_uid, f"⚠️ {amount:,} میوپوینت از کیف پول شما کسر شد.\nموجودی: {new_bal:,}", reply_markup=main_kb(target_uid))
    except Exception:
        pass

async def do_bonus_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    db_execute("UPDATE users SET total_reward = total_reward + %s WHERE user_id=%s", (amount, target_uid))
    add_transaction(target_uid, amount, "bonus", "جایزه دستی از ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} به جایزه‌های کاربر {target_uid} اضافه شد.", reply_markup=admin_kb())

async def do_add_ref_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        count = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if count <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    db_execute("UPDATE users SET referrals_count = referrals_count + %s WHERE user_id=%s", (count, target_uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ {count} رفرال به کاربر {target_uid} اضافه شد.", reply_markup=admin_kb())

async def do_note_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    note = update.message.text
    db_execute("UPDATE users SET notes=%s WHERE user_id=%s", (note, target_uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ یادداشت ذخیره شد.", reply_markup=admin_kb())

async def do_charge_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال شارژ {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET wallet = wallet + %s WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, amount, "add", "شارژ همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Charge all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت به {success} کاربر اضافه شد.", reply_markup=admin_kb())

async def do_bonus_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال افزودن جایزه به {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET total_reward = total_reward + %s WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, amount, "bonus", "جایزه همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Bonus all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت به جایزه‌های {success} کاربر اضافه شد.", reply_markup=admin_kb())

async def do_deduct_bonus_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال کسر جایزه از {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET total_reward = GREATEST(total_reward - %s, 0) WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, -amount, "sub", "کسر جایزه همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Deduct bonus all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت از جایزه‌های {success} کاربر کسر شد.", reply_markup=admin_kb())
async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id

    not_joined = await check_membership(context, uid)
    if not_joined:
        txt = "⚠️ برای استفاده از ربات، ابتدا باید در کانال‌های زیر عضو شوید:\n\n"
        for ch in not_joined:
            txt += f"📢 {ch['id']}\n"
        txt += "\nپس از عضویت، روی دکمه «✅ تلاش مجدد» بزنید."
        await update.message.reply_text(txt, reply_markup=join_kb(not_joined))
        return

    state, data = get_state(uid)

    if state == "ADMIN_REPLY":
        return await do_send_reply(update, context, uid, data)
    if state == "WALLET_AMOUNT":
        return await do_wallet_amount(update, context, uid)
    if state == "ADMIN_BROADCAST":
        return await do_broadcast(update, context, uid)
    if state == "ADMIN_SET_PRICE_HIGH":
        return await do_set_price(update, context, uid, "price_high")
    if state == "ADMIN_SET_PRICE_LOW":
        return await do_set_price(update, context, uid, "price_low")
    if state == "SUPPORT_MESSAGE":
        return await do_support_message(update, context, uid)
    if state == "BUY_AMOUNT":
        return await do_buy_amount(update, context, uid, data)
    if state == "BUY_CARD":
        return await do_buy_card(update, context, uid, data)
    if state == "WITHDRAW_AMOUNT":
        return await do_withdraw_amount(update, context, uid)
    if state == "WITHDRAW_CARD":
        return await do_withdraw_card(update, context, uid, data)
    if state == "ADMIN_SEARCH":
        return await do_search_user(update, context, uid)
    if state == "ADMIN_MSG_USER":
        return await do_msg_user(update, context, uid, data)
    if state == "ADMIN_CHARGE_USER":
        return await do_charge_user(update, context, uid, data)
    if state == "ADMIN_DEDUCT_USER":
        return await do_deduct_user(update, context, uid, data)
    if state == "ADMIN_BONUS_USER":
        return await do_bonus_user(update, context, uid, data)
    if state == "ADMIN_NOTE_USER":
        return await do_note_user(update, context, uid, data)
    if state == "ADMIN_REF_USER":
        return await do_add_ref_user(update, context, uid, data)
    if state == "ADMIN_CHARGE_ALL":
        return await do_charge_all(update, context, uid)
    if state == "ADMIN_BONUS_ALL":
        return await do_bonus_all(update, context, uid)
    if state == "ADMIN_DEDUCT_BONUS_ALL":
        return await do_deduct_bonus_all(update, context, uid)

    if text == "🛒 خرید میو پوینت":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("بالای 100 میلیون", callback_data="cat_high")],
            [InlineKeyboardButton("پایین 100 میلیون", callback_data="cat_low")]
        ])
        await update.message.reply_text("لطفا یک دسته را انتخاب کنید:", reply_markup=kb)
    elif text == "💰 کیف پول":
        bal = get_wallet(uid)
        await update.message.reply_text(f"💰 موجودی کیف پول شما: {bal:,} میوپوینت", reply_markup=wallet_kb())
    elif text == "📞 پشتیبانی":
        set_state(uid, "SUPPORT_MESSAGE")
        await update.message.reply_text("شما در حال تیکت به پشتیبانی هستید\nپیام خود را ارسال کنید:", reply_markup=cancel_kb())
    elif text == "👥 دعوت دوستان":
        get_wallet(uid)
        user = get_user(uid)
        ref_code = user[2]
        ref_count = user[4]
        total_reward = user[5]
        link = f"https://t.me/{BOT_USERNAME}?start={ref_code}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 دریافت جایزه", callback_data="withdraw")]
        ])
        await update.message.reply_text(
            f"👥 دعوت دوستان\n\n"
            f"🔗 کد اختصاصی شما:\n`{link}`\n\n"
            f"📊 تعداد دعوت‌ها: {ref_count}\n"
            f"🎁 جایزه هر دعوت: {REFERRAL_REWARD:,} میوپوینت\n"
            f"💰 مجموع جایزه‌ها: {total_reward:,} میوپوینت\n"
            f"⚠️ حداقل دریافت: {MIN_WITHDRAW:,} میوپوینت\n"
            f"⚠️ حداقل دعوت: {MIN_REFERRALS}\n\n"
            f"برای دریافت جایزه از دکمه زیر استفاده کنید:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    elif text == "🤝 چطور اعتماد کنم":
        await update.message.reply_text(
            "ما یک چنل داریم که در آن رضایت‌ها گذاشته می‌شوند\n"
            "https://t.me/Meow_Point_Free\n\n"
            "برای دیدن رضایت‌ها در چنل سرچ کنید #رضایت\n"
            "و اگر سوالی داشتید در پی وی بگید @Goooorba1234",
            reply_markup=main_kb(uid)
        )
    elif text == "📊 موجودی ما چقدره":
        await update.message.reply_text(
            "📊 موجودی ما چقدره؟\n\n"
            "موجودی ما تقریبا نامحدوده و هرچقدر بخواید می‌تونیم براتون جور کنیم.\n"
            "اما اگه مقداری که بخواید خیلی بالا باشه، یه کم طول می‌کشه تا براتون بزنیم.",
            reply_markup=main_kb(uid)
        )
    elif text == "📖 راهنما":
        await update.message.reply_text(
            "راهنمای خرید میوپوینت\n\n"
            "اول باید کیف پول را شارژ کنید\n"
            "وارد بخش کیف پول شوید و آن را شارژ کنید\n"
            "بعد از شارژ کردن به بخش خرید میو پوینت بروید\n\n"
            "اگر سوالی داشتید پی وی بگید @Goooorba1234",
            reply_markup=main_kb(uid)
        )
    elif text == "🔐 پنل ادمین" and is_admin(uid):
        await update.message.reply_text("🔐 پنل ادمین\n\nلطفا یک گزینه را انتخاب کنید:", reply_markup=admin_kb())
    elif text == "👥 لیست کاربران" and is_admin(uid):
        await show_users_page(update, context, 0)
    elif text == "💰 شارژ همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_CHARGE_ALL")
        await update.message.reply_text("💰 مقدار شارژ برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "🎁 جایزه همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_BONUS_ALL")
        await update.message.reply_text("🎁 مقدار جایزه رفرال برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "💸 کسر جایزه از همه کاربران" and is_admin(uid):
        set_state(uid, "ADMIN_DEDUCT_BONUS_ALL")
        await update.message.reply_text("💸 مقدار کسر جایزه برای همه کاربران رو وارد کن:", reply_markup=cancel_kb())
    elif text == "💳 شارژ کیف پول":
        set_state(uid, "WALLET_AMOUNT")
        await update.message.reply_text(
            f"شما در حال کارت به کارت هستید\n"
            f"شماره کارت: {CARD_NUMBER}\n"
            f"بنام {CARD_NAME}\n\n"
            f"لطفا مبلغ پرداخت را وارد کنید:"
        )
    elif text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    elif text == "📢 پیام همگانی" and is_admin(uid):
        set_state(uid, "ADMIN_BROADCAST")
        await update.message.reply_text("متن پیام همگانی را وارد کنید:", reply_markup=cancel_kb())
    elif text == "💵 قیمت بالای 100" and is_admin(uid):
        set_state(uid, "ADMIN_SET_PRICE_HIGH")
        await update.message.reply_text(f"قیمت فعلی بالای 100: {get_price_high():,} تومان\n\nقیمت جدید:", reply_markup=cancel_kb())
    elif text == "💵 قیمت پایین 100" and is_admin(uid):
        set_state(uid, "ADMIN_SET_PRICE_LOW")
        await update.message.reply_text(f"قیمت فعلی پایین 100: {get_price_low():,} تومان\n\nقیمت جدید:", reply_markup=cancel_kb())
    else:
        await update.message.reply_text("لطفا از دکمه‌های زیر استفاده کنید:", reply_markup=main_kb(uid))

async def show_users_page(update, context, page):
    total = get_users_count()
    users = get_users_page(page)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    text = f"👥 لیست کاربران ({total} کاربر)\nصفحه {page+1} از {total_pages}\n\n"
    buttons = []
    for u in users:
        uid_u, full_name, username, wallet = u
        if full_name and full_name.strip():
            name = full_name
        else:
            name = f"کاربر {uid_u}"
        buttons.append([InlineKeyboardButton(f"{name} - {wallet:,}", callback_data=f"um_view_{uid_u}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"users_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"users_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔍 سرچ", callback_data="users_search")])
    kb = InlineKeyboardMarkup(buttons)
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb)

async def do_search_user(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    query = update.message.text.strip()
    results = search_users(query)
    clear_state(uid)
    if not results:
        await update.message.reply_text("❌ کاربری پیدا نشد.", reply_markup=admin_kb())
        return
    text = f"🔍 نتایج سرچ ({len(results)} کاربر):\n\n"
    buttons = []
    for u in results:
        uid_u, full_name, username, wallet = u
        name = full_name if full_name and full_name.strip() else f"کاربر {uid_u}"
        buttons.append([InlineKeyboardButton(f"{name} - {wallet:,}", callback_data=f"um_view_{uid_u}")])
    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(text, reply_markup=kb)
    await update.message.reply_text("برای بازگشت:", reply_markup=admin_kb())

async def do_msg_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    text = update.message.text
    try:
        await context.bot.send_message(target_uid, f"📢 پیام از ادمین:\n\n{text}", reply_markup=main_kb(target_uid))
        await update.message.reply_text(f"✅ پیام به کاربر {target_uid} ارسال شد.", reply_markup=admin_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}", reply_markup=admin_kb())
    clear_state(uid)

async def do_charge_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    new_bal = add_wallet(target_uid, amount, f"شارژ دستی توسط ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} به کاربر {target_uid} اضافه شد.\nموجودی: {new_bal:,}", reply_markup=admin_kb())
    try:
        await context.bot.send_message(target_uid, f"🎁 {amount:,} میوپوینت به کیف پول شما اضافه شد!\nموجودی جدید: {new_bal:,}", reply_markup=main_kb(target_uid))
    except Exception:
        pass

async def do_deduct_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    new_bal = add_wallet(target_uid, -amount, f"کسر دستی توسط ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} از کاربر {target_uid} کسر شد.\nموجودی: {new_bal:,}", reply_markup=admin_kb())
    try:
        await context.bot.send_message(target_uid, f"⚠️ {amount:,} میوپوینت از کیف پول شما کسر شد.\nموجودی: {new_bal:,}", reply_markup=main_kb(target_uid))
    except Exception:
        pass

async def do_bonus_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    db_execute("UPDATE users SET total_reward = total_reward + %s WHERE user_id=%s", (amount, target_uid))
    add_transaction(target_uid, amount, "bonus", "جایزه دستی از ادمین")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} به جایزه‌های کاربر {target_uid} اضافه شد.", reply_markup=admin_kb())

async def do_add_ref_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        count = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if count <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    db_execute("UPDATE users SET referrals_count = referrals_count + %s WHERE user_id=%s", (count, target_uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ {count} رفرال به کاربر {target_uid} اضافه شد.", reply_markup=admin_kb())

async def do_note_user(update, context, uid, data):
    target_uid = int(data)
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    note = update.message.text
    db_execute("UPDATE users SET notes=%s WHERE user_id=%s", (note, target_uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ یادداشت ذخیره شد.", reply_markup=admin_kb())

async def do_charge_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال شارژ {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET wallet = wallet + %s WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, amount, "add", "شارژ همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Charge all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت به {success} کاربر اضافه شد.", reply_markup=admin_kb())

async def do_bonus_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال افزودن جایزه به {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET total_reward = total_reward + %s WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, amount, "bonus", "جایزه همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Bonus all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت به جایزه‌های {success} کاربر اضافه شد.", reply_markup=admin_kb())

async def do_deduct_bonus_all(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        amount = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    users = get_all_users()
    await update.message.reply_text(f"⏳ در حال کسر جایزه از {len(users)} کاربر...")
    success = 0
    for u in users:
        try:
            db_execute("UPDATE users SET total_reward = GREATEST(total_reward - %s, 0) WHERE user_id=%s", (amount, u))
            db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
                       (u, -amount, "sub", "کسر جایزه همگانی از ادمین"))
            success += 1
        except Exception as e:
            logging.error(f"Deduct bonus all error for {u}: {e}")
    clear_state(uid)
    await update.message.reply_text(f"✅ {amount:,} میوپوینت از جایزه‌های {success} کاربر کسر شد.", reply_markup=admin_kb())
async def do_withdraw_amount(update, context, uid):
    text = fa_to_en(update.message.text.strip()).replace(",", "").replace("،", "")
    user = get_user(uid)
    total_reward = user[5]
    try:
        amount = int(text)
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"لطفا مقدار بالای {MIN_WITHDRAW:,} وارد کنید.")
            return
        if amount > total_reward:
            await update.message.reply_text(f"❌ مقدار درخواست از مجموع جایزه‌های شما ({total_reward:,}) بیشتره.")
            return
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    set_state(uid, "WITHDRAW_CARD", str(amount))
    await update.message.reply_text("لطفا شماره کارت خود را وارد کنید:")

async def do_withdraw_card(update, context, uid, data):
    card = update.message.text.strip()
    amount = int(data)
    user = get_user(uid)
    ref_count = user[4]
    # کسر ۵ رفرال و مقدار جایزه درخواستی
    db_execute("UPDATE users SET referrals_count = GREATEST(referrals_count - %s, 0), total_reward = GREATEST(total_reward - %s, 0) WHERE user_id=%s",
               (MIN_REFERRALS, amount, uid))
    db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)",
               (uid, -amount, "sub", "دریافت جایزه دعوت"))
    db_execute("INSERT INTO withdraw_requests (user_id, amount, card) VALUES (%s, %s, %s)", (uid, amount, card))
    r = db_execute("SELECT id FROM withdraw_requests WHERE user_id=%s ORDER BY id DESC LIMIT 1", (uid,), fetch=True)
    req_id = r[0]
    clear_state(uid)
    await update.message.reply_text("✅ درخواست شما ثبت شد.", reply_markup=main_kb(uid))
    admin_text = (
        f"💰 درخواست دریافت جایزه\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {update.effective_user.full_name}\n"
        f"🔗 یوزرنیم: @{update.effective_user.username if update.effective_user.username else 'ندارد'}\n"
        f"👥 تعداد دعوت قبل: {ref_count}\n"
        f"💵 مقدار: {amount:,} میوپوینت\n"
        f"💳 کارت: {card}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ واریز شد", callback_data=f"paid_{req_id}_{uid}")]])
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)

async def do_broadcast(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    text = update.message.text
    users = get_all_users()
    sent, failed = 0, 0
    await update.message.reply_text(f"در حال ارسال به {len(users)} کاربر...")
    for u in users:
        try:
            await context.bot.send_message(u, f"📢 اطلاعیه:\n\n{text}")
            sent += 1
        except Exception:
            failed += 1
    clear_state(uid)
    await update.message.reply_text(f"✅ ارسال شد به {sent}\n❌ ناموفق: {failed}", reply_markup=admin_kb())

async def do_set_price(update, context, uid, key):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return
    try:
        val = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if val <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    set_setting(key, val)
    clear_state(uid)
    title = "بالای 100" if key == "price_high" else "پایین 100"
    await update.message.reply_text(f"✅ قیمت {title} به {val:,} تومان تغییر کرد.", reply_markup=admin_kb())

async def do_support_message(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
        return
    user = update.effective_user
    text = update.message.text
    admin_text = (
        f"💬 تیکت جدید\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {user.full_name}\n"
        f"🔗 یوزرنیم: @{user.username if user.username else 'ندارد'}\n\n"
        f"📝 پیام:\n{text}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ پاسخ", callback_data=f"reply_{uid}")]])
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)
    clear_state(uid)
    await update.message.reply_text("✅ پیام شما ارسال شد.", reply_markup=main_kb(uid))

async def do_send_reply(update, context, uid, data):
    target_uid = int(data)
    text = update.message.text
    try:
        await context.bot.send_message(target_uid, f"📩 پاسخ پشتیبانی:\n\n{text}", reply_markup=main_kb(target_uid))
        await update.message.reply_text(f"✅ پاسخ ارسال شد.", reply_markup=main_kb(uid))
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")
    clear_state(uid)

async def do_buy_amount(update, context, uid, cat):
    text = fa_to_en(update.message.text.strip())
    try:
        amount = float(text)
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    threshold = get_threshold()
    if cat == "high" and amount < threshold:
        await update.message.reply_text(f"لطفا مقدار بالای {threshold} میلیون وارد کنید.")
        return
    if cat == "low" and amount >= threshold:
        await update.message.reply_text(f"لطفا مقدار پایین {threshold} میلیون وارد کنید.")
        return
    price = get_price_high() if cat == "high" else get_price_low()
    total = int(amount * price)
    set_state(uid, "BUY_CARD", f"{amount}|{total}")
    await update.message.reply_text(
        f"✅ مقدار: {amount:g} میلیون\n"
        f"💵 قیمت هر میلیون: {price:,} تومان\n"
        f"💰 مبلغ کل: {total:,} تومان\n\n"
        f"لطفا شماره کارت میویی خود را وارد کنید:"
    )

async def do_buy_card(update, context, uid, data):
    card = update.message.text.strip()
    parts = data.split("|")
    amount = float(parts[0])
    total = int(parts[1])
    set_state(uid, "BUY_PAY", f"{card}|{amount}|{total}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 پرداخت با کیف پول", callback_data="pay_wallet")]])
    await update.message.reply_text(
        f"شماره کارت شما ثبت شد: {card}\nمبلغ قابل پرداخت: {total:,} تومان\n\nبرای پرداخت روی دکمه زیر بزنید:",
        reply_markup=kb
    )

async def do_wallet_amount(update, context, uid):
    text = fa_to_en(update.message.text.strip()).replace(",", "").replace("،", "")
    try:
        amount = int(text)
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return
    set_state(uid, "WALLET_PHOTO", str(amount))
    await update.message.reply_text("لطفا عکس رسید پرداخت را ارسال کنید:")
async def on_callback(update, context):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    if data == "check_join":
        await query.answer()
        not_joined = await check_membership(context, uid)
        if not_joined:
            text = "⚠️ هنوز عضو کانال‌های زیر نشدی:\n\n"
            for ch in not_joined:
                text += f"📢 {ch['id']}\n"
            text += "\nپس از عضویت، روی دکمه «✅ تلاش مجدد» بزنید."
            try:
                await query.edit_message_text(text, reply_markup=join_kb(not_joined))
            except:
                await query.message.reply_text(text, reply_markup=join_kb(not_joined))
            return
        else:
            await process_start_logic(update, context, uid, query.from_user)
            try:
                await query.edit_message_text("✅ عضویت شما تایید شد. با تشکر!")
            except:
                pass
            return

    if data.startswith("users_page_"):
        await query.answer()
        if not is_admin(uid): return
        page = int(data.replace("users_page_", ""))
        await show_users_page(update, context, page)
    elif data == "users_search":
        await query.answer()
        if not is_admin(uid): return
        set_state(uid, "ADMIN_SEARCH")
        await query.message.reply_text("🔍 اسم یا آیدی یا یوزرنیم کاربر رو وارد کن:", reply_markup=cancel_kb())
    elif data.startswith("um_view_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_view_", ""))
        user = get_user(target_uid)
        if not user:
            await query.message.reply_text("❌ کاربر پیدا نشد.")
            return
        uid_u, wallet, ref_code, referred_by, ref_count, total_reward, full_name, username, joined_at, notes = user
        name_display = full_name if full_name and full_name.strip() else f"کاربر {uid_u}"
        text = (
            f"👤 اطلاعات کاربر\n\n"
            f"🆔 آیدی: {uid_u}\n"
            f"👤 نام: {name_display}\n"
            f"🔗 یوزرنیم: @{username if username else 'ندارد'}\n"
            f"💰 موجودی: {wallet:,} میوپوینت\n"
            f"🎁 جایزه دعوت: {total_reward:,}\n"
            f"👥 تعداد دعوت: {ref_count}\n"
            f"📅 تاریخ عضویت: {joined_at}\n\n"
            f"گزینه‌ها:"
        )
        await query.message.reply_text(text, reply_markup=user_manage_kb(target_uid))
    elif data.startswith("um_charge_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_charge_", ""))
        set_state(uid, "ADMIN_CHARGE_USER", str(target_uid))
        await query.message.reply_text(f"💰 مقدار شارژ برای کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_deduct_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_deduct_", ""))
        set_state(uid, "ADMIN_DEDUCT_USER", str(target_uid))
        await query.message.reply_text(f"💸 مقدار کسر از کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_ref_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_ref_", ""))
        set_state(uid, "ADMIN_REF_USER", str(target_uid))
        await query.message.reply_text(f"👥 تعداد رفرال برای کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_msg_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_msg_", ""))
        set_state(uid, "ADMIN_MSG_USER", str(target_uid))
        await query.message.reply_text(f"📢 متن پیام برای کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_bonus_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_bonus_", ""))
        set_state(uid, "ADMIN_BONUS_USER", str(target_uid))
        await query.message.reply_text(f"🎁 مقدار جایزه دعوت برای کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_note_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_note_", ""))
        set_state(uid, "ADMIN_NOTE_USER", str(target_uid))
        await query.message.reply_text(f"📝 یادداشت برای کاربر {target_uid}:", reply_markup=cancel_kb())
    elif data.startswith("um_info_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_info_", ""))
        user = get_user(target_uid)
        if not user: return
        uid_u, wallet, ref_code, referred_by, ref_count, total_reward, full_name, username, joined_at, notes = user
        await query.message.reply_text(
            f"📊 اطلاعات کامل کاربر\n\n"
            f"🆔 آیدی: {uid_u}\n"
            f"👤 نام: {full_name or 'ندارد'}\n"
            f"🔗 یوزرنیم: @{username if username else 'ندارد'}\n"
            f"💰 موجودی: {wallet:,}\n"
            f"🎁 جایزه دعوت: {total_reward:,}\n"
            f"👥 تعداد دعوت: {ref_count}\n"
            f"🔗 کد معرف: {ref_code}\n"
            f"👤 معرف: {referred_by}\n"
            f"📅 عضویت: {joined_at}\n"
            f"📝 یادداشت: {notes or 'ندارد'}"
        )
    elif data.startswith("um_orders_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_orders_", ""))
        rows = db_execute_all("SELECT amount, type, description, created_at FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT 20", (target_uid,))
        if not rows:
            await query.message.reply_text("📜 سفارشی ثبت نشده.")
            return
        text = f"📜 آخرین تراکنش‌های کاربر {target_uid}:\n\n"
        for r in rows:
            text += f"• {r[1]} | {r[0]:,} | {r[2]} | {r[3]}\n"
        await query.message.reply_text(text)
    elif data.startswith("um_history_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_history_", ""))
        rows = db_execute_all("SELECT amount, type, description, created_at FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT 30", (target_uid,))
        if not rows:
            await query.message.reply_text("📊 تراکنشی ثبت نشده.")
            return
        text = f"📊 تاریخچه تراکنش‌های کاربر {target_uid}:\n\n"
        for r in rows:
            text += f"• {r[3]} | {r[1]} | {r[0]:,} | {r[2]}\n"
        await query.message.reply_text(text)
    elif data.startswith("um_delete_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("um_delete_", ""))
        db_execute("DELETE FROM users WHERE user_id=%s", (target_uid,))
        db_execute("DELETE FROM states WHERE user_id=%s", (target_uid,))
        db_execute("DELETE FROM transactions WHERE user_id=%s", (target_uid,))
        db_execute("DELETE FROM pending_refs WHERE user_id=%s", (target_uid,))
        await query.message.reply_text(f"🗑 کاربر {target_uid} حذف شد.")
    elif data == "cat_high":
        await query.answer()
        set_state(uid, "BUY_AMOUNT", "high")
        await query.edit_message_text("شما «بالای 100 میلیون» را انتخاب کردید.\nحالا مقدار را به میلیون وارد کنید:")
    elif data == "cat_low":
        await query.answer()
        set_state(uid, "BUY_AMOUNT", "low")
        await query.edit_message_text("شما «پایین 100 میلیون» را انتخاب کردید.\nحالا مقدار را به میلیون وارد کنید:")
    elif data == "pay_wallet":
        await query.answer()
        await do_pay_wallet(query, context, uid)
    elif data == "withdraw":
        await query.answer()
        get_wallet(uid)
        user = get_user(uid)
        ref_count = user[4]
        if ref_count < MIN_REFERRALS:
            await query.message.reply_text(f"❌ رفرال‌ها کم هستند.\nشما {ref_count} دعوت دارید. حداقل {MIN_REFERRALS} دعوت لازم است.")
            return
        set_state(uid, "WITHDRAW_AMOUNT")
        await query.message.reply_text(f"مقدار درخواست را وارد کنید:\n(حداقل {MIN_WITHDRAW:,} میوپوینت)")
    elif data.startswith("deliver_"):
        await query.answer()
        if not is_admin(uid):
            await query.answer("فقط ادمین", show_alert=True)
            return
        target_uid = int(data.replace("deliver_", ""))
        try:
            await context.bot.send_message(target_uid, "✅ میو پوینت شما واریز شد!\n\nاز خرید شما متشکریم. 🌸")
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"✅ پیام واریز به کاربر {target_uid} ارسال شد.")
        except Exception as e:
            await query.message.reply_text(f"❌ خطا: {e}")
    elif data.startswith("paid_"):
        await query.answer()
        if not is_admin(uid): return
        parts = data.replace("paid_", "").split("_")
        req_id = int(parts[0])
        target_uid = int(parts[1])
        r = db_execute("SELECT amount FROM withdraw_requests WHERE id=%s", (req_id,), fetch=True)
        if r:
            amount = r[0]
            try:
                await context.bot.send_message(target_uid, f"✅ جایزه {amount:,} میوپوینت شما واریز شد!")
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ پیام به کاربر {target_uid} ارسال شد.")
            except Exception as e:
                logging.error(e)
    elif data.startswith("reply_"):
        await query.answer()
        if not is_admin(uid): return
        target_uid = int(data.replace("reply_", ""))
        set_state(uid, "ADMIN_REPLY", str(target_uid))
        await query.message.reply_text(f"✍️ پاسخ خود را بنویسید:")
    elif data.startswith("charge_ok_"):
        await do_charge(query, context, data, True)
    elif data.startswith("charge_no_"):
        await do_charge(query, context, data, False)
    else:
        await query.answer()

async def do_pay_wallet(query, context, uid):
    state, data = get_state(uid)
    if state != "BUY_PAY":
        await query.edit_message_text("❌ خطا: لطفا دوباره از ابتدا تلاش کنید.")
        return
    parts = data.split("|")
    card, amount, total = parts[0], float(parts[1]), int(parts[2])
    bal = get_wallet(uid)
    if bal < total:
        await query.edit_message_text(f"❌ موجودی کافی نمیباشد.\nموجودی: {bal:,}\nمبلغ لازم: {total:,}")
        clear_state(uid)
        return
    new_bal = add_wallet(uid, -total, f"خرید {amount:g} میلیون میو")
    admin_text = (
        f"🛒 سفارش جدید\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {query.from_user.full_name}\n"
        f"💳 کارت میویی: {card}\n"
        f"📊 مقدار: {amount:g} میلیون\n"
        f"💰 مبلغ: {total:,} تومان\n"
        f"💼 موجودی: {new_bal:,}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ واریز شد", callback_data=f"deliver_{uid}")]])
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)
    await query.edit_message_text(
        f"✅ پرداخت انجام شد.\nمبلغ {total:,} از کیف پول کسر شد.\nموجودی جدید: {new_bal:,}\n\nسفارش ثبت شد."
    )
    await context.bot.send_message(uid, "به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    clear_state(uid)

async def do_charge(query, context, data, approve):
    if approve:
        charge_id = int(data.replace("charge_ok_", ""))
    else:
        charge_id = int(data.replace("charge_no_", ""))
    row = db_execute("SELECT user_id, amount, status FROM pending_charges WHERE id=%s", (charge_id,), fetch=True)
    if not row:
        await query.answer("یافت نشد.", show_alert=True)
        return
    uid, amount, status = row
    if status != "pending":
        await query.answer("قبلا پردازش شده.", show_alert=True)
        return
    if approve:
        add_wallet(uid, amount, "شارژ کیف پول")
        db_execute("UPDATE pending_charges SET status='approved' WHERE id=%s", (charge_id,))
        await query.edit_message_caption(f"✅ تایید شد — {amount:,} به کاربر {uid} اضافه شد.")
        try:
            await context.bot.send_message(uid, f"✅ کیف پول شما به مبلغ {amount:,} شارژ شد.", reply_markup=main_kb(uid))
        except Exception as e:
            logging.error(e)
    else:
        db_execute("UPDATE pending_charges SET status='rejected' WHERE id=%s", (charge_id,))
        await query.edit_message_caption(f"❌ رد شد — درخواست شارژ کاربر {uid} رد شد.")
        try:
            await context.bot.send_message(uid, "❌ رسید شما تایید نشد.", reply_markup=main_kb(uid))
        except Exception as e:
            logging.error(e)

async def on_photo(update, context):
    uid = update.effective_user.id
    state, data = get_state(uid)
    if state != "WALLET_PHOTO":
        return
    amount = int(data)
    photo_id = update.message.photo[-1].file_id
    db_execute("INSERT INTO pending_charges (user_id, amount, photo_id) VALUES (%s, %s, %s)", (uid, amount, photo_id))
    r = db_execute("SELECT id FROM pending_charges WHERE user_id=%s ORDER BY id DESC LIMIT 1", (uid,), fetch=True)
    charge_id = r[0]
    clear_state(uid)
    await update.message.reply_text("لطفا منتظر تایید باشید...", reply_markup=main_kb(uid))
    admin_text = (
        f"💳 درخواست شارژ کیف پول\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {update.effective_user.full_name}\n"
        f"💰 مبلغ: {amount:,} تومان"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید", callback_data=f"charge_ok_{charge_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"charge_no_{charge_id}")]
    ])
    try:
        await context.bot.send_photo(ADMIN_ID, photo=photo_id, caption=admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(on_callback))
app.add_handler(MessageHandler(filters.PHOTO, on_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

async def start_web_server():
    async def handle(request):
        return web.Response(text="Bot is running ✅")
    web_app = web.Application()
    web_app.router.add_get('/', handle)
    web_app.router.add_route('*', '/ping', handle)
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

async def main():
    await start_web_server()
    async with app:
        await app.start()
        await app.updater.start_polling()
        print("Bot started... ✅")
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
