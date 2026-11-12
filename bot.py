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

TOKEN = "8985946672:AAEE0OGCl9-et8VKhN_PmdGOWzgQ81xduC8"
DATABASE_URL = "postgresql://meowuser:AnrVtkzHOf2bmKtYjDSLuOaRzwnvv9P8@dpg-dbmpbkbm8hqs73b3f560-a/meowbot_ot06"
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
        """CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, wallet BIGINT DEFAULT 0, ref_code VARCHAR(10) UNIQUE, referred_by BIGINT, referrals_count INTEGER DEFAULT 0, total_reward BIGINT DEFAULT 0, full_name TEXT, username TEXT, joined_at TIMESTAMP DEFAULT NOW(), notes TEXT DEFAULT '')""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP DEFAULT NOW()""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''""",
        """CREATE TABLE IF NOT EXISTS pending_charges (id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, photo_id TEXT, card_photo_id TEXT, status TEXT DEFAULT 'pending')""",
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""",
        """CREATE TABLE IF NOT EXISTS withdraw_requests (id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, card TEXT, status TEXT DEFAULT 'pending')""",
        """CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, type TEXT, description TEXT, created_at TIMESTAMP DEFAULT NOW())""",
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
    if state == "WALLET_AMOUNT": return await do_wallet_amount(update, context, uid)
    if state == "SUPPORT_MESSAGE": return await do_support_message(update, context, uid)
    if state == "BUY_AMOUNT": return await do_buy_amount(update, context, uid, data)
    if state == "BUY_CARD": return await do_buy_card(update, context, uid, data)
    if state == "WITHDRAW_AMOUNT": return await do_withdraw_amount(update, context, uid)
    if state == "WITHDRAW_CARD": return await do_withdraw_card(update, context, uid, data)

    if text == "🛒 خرید میو پوینت":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("بالای 100 میلیون", callback_data="cat_high")], [InlineKeyboardButton("پایین 100 میلیون", callback_data="cat_low")]])
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
        link = f"https://t.me/{context.bot.username}?start={ref_code}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 دریافت جایزه", callback_data="withdraw")]])
        await update.message.reply_text(f"👥 دعوت دوستان\n\n🔗 کد اختصاصی شما:\n`{link}`\n\n📊 تعداد دعوت‌ها: {ref_count}\n🎁 جایزه هر دعوت: {REFERRAL_REWARD:,} میوپوینت\n💰 مجموع جایزه‌ها: {total_reward:,} میوپوینت\n⚠️ حداقل دریافت: {MIN_WITHDRAW:,} میوپوینت\n⚠️ حداقل دعوت: {MIN_REFERRALS}\n\nبرای دریافت جایزه از دکمه زیر استفاده کنید:", reply_markup=kb, parse_mode="Markdown")
    elif text == "🤝 چطور اعتماد کنم":
        await update.message.reply_text("ما یک چنل داریم که در آن رضایت‌ها گذاشته می‌شوند\nhttps://t.me/Meow_Point_Free\n\nبرای دیدن رضایت‌ها در چنل سرچ کنید #رضایت\nو اگر سوالی داشتید در پی وی بگید @Goooorba1234", reply_markup=main_kb(uid))
    elif text == "📊 موجودی ما چقدره":
        await update.message.reply_text("📊 موجودی ما چقدره؟\n\nموجودی ما تقریبا نامحدوده و هرچقدر بخواید می‌تونیم براتون جور کنیم.\nاما اگه مقداری که بخواید خیلی بالا باشه، یه کم طول می‌کشه تا براتون بزنیم.", reply_markup=main_kb(uid))
    elif text == "📖 راهنما":
        await update.message.reply_text("راهنمای خرید میوپوینت\n\nاول باید کیف پول را شارژ کنید\nوارد بخش کیف پول شوید و آن را شارژ کنید\nبعد از شارژ کردن به بخش خرید میو پوینت بروید\n\nاگر سوالی داشتید پی وی بگید @Goooorba1234", reply_markup=main_kb(uid))
    elif text == "🔐 پنل ادمین" and is_admin(uid):
        await update.message.reply_text("🔐 پنل ادمین\n\nلطفا یک گزینه را انتخاب کنید:", reply_markup=admin_kb())
    elif text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    else:
        await update.message.reply_text("لطفا از دکمه‌ها استفاده کنید:", reply_markup=main_kb(uid))

async def do_wallet_amount(update, context, uid):
    text = fa_to_en(update.message.text.strip()).replace(",", "").replace("،", "")
    try:
        amount = int(text)
        if amount <= 0: raise ValueError
    except: return await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
    set_state(uid, "WALLET_CARD", str(amount))
    await update.message.reply_text("برای اینکه فیشینگ اتفاق نیفته، اول عکس از کارتی که باهاش پرداخت می‌کنید را بفرستید.\n⚠️ توجه: اطلاعاتی مثل CVV2 نیاز نیست و فقط اسم و شماره کارت کافیه.")

async def do_support_message(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    user = update.effective_user
    text = update.message.text
    admin_text = f"💬 تیکت جدید\n\n🆔 آیدی: {uid}\n👤 نام: {user.full_name}\n🔗 یوزرنیم: @{user.username if user.username else 'ندارد'}\n\n📝 پیام:\n{text}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ پاسخ", callback_data=f"reply_{uid}")]])
    try: await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except: pass
    clear_state(uid)
    await update.message.reply_text("✅ پیام شما ارسال شد.", reply_markup=main_kb(uid))

async def do_buy_amount(update, context, uid, cat):
    text = fa_to_en(update.message.text.strip())
    try:
        amount = float(text)
        if amount <= 0: raise ValueError
    except: return await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
    threshold = get_threshold()
    if cat == "high" and amount < threshold: return await update.message.reply_text(f"لطفا مقدار بالای {threshold} میلیون وارد کنید.")
    if cat == "low" and amount >= threshold: return await update.message.reply_text(f"لطفا مقدار پایین {threshold} میلیون وارد کنید.")
    price = get_price_high() if cat == "high" else get_price_low()
    total = int(amount * price)
    set_state(uid, "BUY_CARD", f"{amount}|{total}")
    await update.message.reply_text(f"✅ مقدار: {amount:g} میلیون\n💵 قیمت هر میلیون: {price:,} تومان\n💰 مبلغ کل: {total:,} تومان\n\nلطفا شماره کارت میویی خود را وارد کنید:")

async def do_buy_card(update, context, uid, data):
    card = update.message.text.strip()
    parts = data.split("|")
    amount = float(parts[0])
    total = int(parts[1])
    set_state(uid, "BUY_PAY", f"{card}|{amount}|{total}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 پرداخت با کیف پول", callback_data="pay_wallet")]])
    await update.message.reply_text(f"شماره کارت شما ثبت شد: {card}\nمبلغ قابل پرداخت: {total:,} تومان\n\nبرای پرداخت روی دکمه زیر بزنید:", reply_markup=kb)

async def do_withdraw_amount(update, context, uid):
    text = fa_to_en(update.message.text.strip()).replace(",", "").replace("،", "")
    user = get_user(uid)
    total_reward = user[5]
    try:
        amount = int(text)
        if amount < MIN_WITHDRAW: return await update.message.reply_text(f"لطفا مقدار بالای {MIN_WITHDRAW:,} وارد کنید.")
        if amount > total_reward: return await update.message.reply_text(f"❌ مقدار درخواست از مجموع جایزه‌های شما ({total_reward:,}) بیشتره.")
    except: return await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
    set_state(uid, "WITHDRAW_CARD", str(amount))
    await update.message.reply_text("لطفا شماره کارت خود را وارد کنید:")

async def do_withdraw_card(update, context, uid, data):
    card = update.message.text.strip()
    amount = int(data)
    user = get_user(uid)
    ref_count = user[4]
    db_execute("UPDATE users SET referrals_count = GREATEST(referrals_count - %s, 0), total_reward = GREATEST(total_reward - %s, 0) WHERE user_id=%s", (MIN_REFERRALS, amount, uid))
    db_execute("INSERT INTO transactions (user_id, amount, type, description) VALUES (%s, %s, %s, %s)", (uid, -amount, "sub", "دریافت جایزه دعوت"))
    db_execute("INSERT INTO withdraw_requests (user_id, amount, card) VALUES (%s, %s, %s)", (uid, amount, card))
    clear_state(uid)
    await update.message.reply_text("✅ درخواست شما ثبت شد.", reply_markup=main_kb(uid))
    admin_text = f"💰 درخواست دریافت جایزه\n\n🆔 آیدی: {uid}\n👤 نام: {update.effective_user.full_name}\n💵 مقدار: {amount:,} میوپوینت\n💳 کارت: {card}"
    try: await context.bot.send_message(ADMIN_ID, admin_text)
    except: pass

async def on_callback(update, context):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id
    if data == "check_join":
        await query.answer()
        not_joined = await check_membership(context, uid)
        if not_joined: await query.edit_message_text("⚠️ هنوز عضو نشدی!", reply_markup=join_kb(not_joined))
        else:
            await process_start_logic(update, context, uid, query.from_user)
            try: await query.delete()
            except: pass
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
        state, d = get_state(uid)
        if state != "BUY_PAY": return await query.edit_message_text("❌ خطا.")
        parts = d.split("|")
        card, amount, total = parts[0], float(parts[1]), int(parts[2])
        bal = get_wallet(uid)
        if bal < total:
            await query.edit_message_text(f"❌ موجودی کافی نیست.\nموجودی: {bal:,}")
            clear_state(uid)
            return
        new_bal = add_wallet(uid, -total, f"خرید {amount:g} میلیون میو")
        admin_text = f"🛒 سفارش جدید\n\n🆔 آیدی: {uid}\n👤 نام: {query.from_user.full_name}\n💳 کارت میویی: {card}\n📊 مقدار: {amount:g} میلیون\n💰 مبلغ: {total:,} تومان\n💼 موجودی: {new_bal:,}"
        try: await context.bot.send_message(ADMIN_ID, admin_text)
        except: pass
        await query.edit_message_text(f"✅ پرداخت انجام شد.\nموجودی جدید: {new_bal:,}\n\nسفارش ثبت شد.")
        await context.bot.send_message(uid, "به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
        clear_state(uid)
    elif data == "withdraw":
        await query.answer()
        set_state(uid, "WITHDRAW_AMOUNT")
        await query.message.reply_text(f"مقدار درخواست را وارد کنید:\n(حداقل {MIN_WITHDRAW:,} میوپوینت)")
    else:
        await query.answer()

async def on_photo(update, context):
    uid = update.effective_user.id
    state, data = get_state(uid)
    photo_id = update.message.photo[-1].file_id
    if state == "WALLET_CARD":
        set_state(uid, "WALLET_PHOTO", f"{data}|{photo_id}")
        return await update.message.reply_text("✅ عکس کارت دریافت شد.\n\nحالا لطفا عکس رسید تراکنش را ارسال کنید:")
    if state == "WALLET_PHOTO":
        parts = data.split("|")
        amount = int(parts[0])
        card_photo_id = parts[1]
        db_execute("INSERT INTO pending_charges (user_id, amount, photo_id, card_photo_id) VALUES (%s, %s, %s, %s)", (uid, amount, photo_id, card_photo_id))
        clear_state(uid)
        await update.message.reply_text("لطفا منتظر تایید باشید...", reply_markup=main_kb(uid))
        admin_text = f"💳 درخواست شارژ\n\n🆔 آیدی: {uid}\n👤 نام: {update.effective_user.full_name}\n💰 مبلغ: {amount:,} تومان"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید", callback_data="charge_ok"), InlineKeyboardButton("❌ رد", callback_data="charge_no")]])
        try:
            await context.bot.send_photo(ADMIN_ID, photo=card_photo_id, caption=admin_text)
            await context.bot.send_photo(ADMIN_ID, photo=photo_id, caption=f"📸 رسید کاربر {uid} - مبلغ: {amount:,} تومان", reply_markup=kb)
        except Exception as e: logging.error(e)
        return

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(on_callback))
app.add_handler(MessageHandler(filters.PHOTO, on_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

async def start_web_server():
    async def handle(request): return web.Response(text="Bot is running ✅")
    web_app = web.Application()
    web_app.router.add_get('/', handle)
    web_app.router.add_route('*', '/ping', handle)
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await start_web_server()
    async with app:
        await app.start()
        await app.updater.start_polling()
        print("Bot started! ✅")
        while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
