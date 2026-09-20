import asyncio
import logging
import os
import random
import string
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

logging.basicConfig(level=logging.INFO)

connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

def db_execute(query, params=None, fetch=False):
    conn = connection_pool.getconn()
    try:
        c = conn.cursor()
        c.execute(query, params)
        if fetch:
            result = c.fetchone()
        else:
            result = None
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
            total_reward BIGINT DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS pending_charges (
            id SERIAL PRIMARY KEY,
            user_id BIGINT, amount BIGINT, photo_id TEXT, status TEXT DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""",
        """CREATE TABLE IF NOT EXISTS withdraw_requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT, amount BIGINT, card TEXT, status TEXT DEFAULT 'pending'
        )"""
    ]
    for q in queries:
        db_execute(q)

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

def add_wallet(uid, delta):
    new = get_wallet(uid) + delta
    db_execute("""INSERT INTO users (user_id, wallet) VALUES (%s, %s)
                  ON CONFLICT (user_id) DO UPDATE SET wallet = EXCLUDED.wallet""", (uid, new))
    return new

def get_user(uid):
    return db_execute("SELECT user_id, wallet, ref_code, referred_by, referrals_count, total_reward FROM users WHERE user_id=%s", (uid,), fetch=True)

def get_user_by_ref(code):
    return db_execute("SELECT user_id FROM users WHERE ref_code=%s", (code,), fetch=True)

def user_exists(uid):
    r = db_execute("SELECT 1 FROM users WHERE user_id=%s", (uid,), fetch=True)
    return r is not None

def get_all_users():
    rows = db_execute_all("SELECT user_id FROM users")
    return [r[0] for r in rows]

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

def main_kb(uid=None):
    rows = [
        [KeyboardButton("🛒 خرید میو پوینت")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("👥 دعوت دوستان"), KeyboardButton("🤝 چطور اعتماد کنم")],
        [KeyboardButton("📖 راهنما")],
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
        [KeyboardButton("💵 قیمت بالای 100"), KeyboardButton("💵 قیمت پایین 100")],
        [KeyboardButton("🔙 بازگشت")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True)

async def start(update, context):
    uid = update.effective_user.id
    args = context.args
    is_new = not user_exists(uid)

    if is_new:
        db_execute("INSERT INTO users (user_id, wallet, ref_code) VALUES (%s, 0, %s)", (uid, gen_ref_code()))
        if args:
            ref_code = args[0]
            ref_owner = get_user_by_ref(ref_code)
            if ref_owner:
                ref_uid = ref_owner[0]
                if ref_uid != uid:
                    db_execute("UPDATE users SET referrals_count=referrals_count+1, total_reward=total_reward+%s WHERE user_id=%s",
                              (REFERRAL_REWARD, ref_uid))
                    try:
                        await context.bot.send_message(
                            ref_uid,
                            f"👤 کاربر {update.effective_user.full_name} با لینک شما وارد شد!\n"
                            f"🎁 {REFERRAL_REWARD:,} میوپوینت به جایزه‌های شما اضافه شد."
                        )
                    except Exception as e:
                        logging.error(e)

    clear_state(uid)
    await update.message.reply_text(
        "به ربات خوش آمدید! 👋\nلطفا یک گزینه را انتخاب کنید:",
        reply_markup=main_kb(uid)
    )

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
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
    db_execute("INSERT INTO withdraw_requests (user_id, amount, card) VALUES (%s, %s, %s)", (uid, amount, card))
    r = db_execute("SELECT id FROM withdraw_requests WHERE user_id=%s ORDER BY id DESC LIMIT 1", (uid,), fetch=True)
    req_id = r[0]
    clear_state(uid)
    await update.message.reply_text("✅ درخواست شما ثبت شد. به زودی بررسی می‌شود.", reply_markup=main_kb(uid))
    admin_text = (
        f"💰 درخواست دریافت جایزه\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {update.effective_user.full_name}\n"
        f"🔗 یوزرنیم: @{update.effective_user.username if update.effective_user.username else 'ندارد'}\n"
        f"👥 تعداد دعوت: {ref_count}\n"
        f"💵 مقدار درخواست: {amount:,} میوپوینت\n"
        f"💳 شماره کارت: {card}"
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
        logging.error(e)
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

    if data == "cat_high":
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
            logging.error(e)
            await query.message.reply_text(f"❌ خطا: {e}")
    elif data.startswith("paid_"):
        await query.answer()
        if not is_admin(uid):
            return
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
        if not is_admin(uid):
            return
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
    new_bal = add_wallet(uid, -total)
    admin_text = (
        f"🛒 سفارش جدید\n\n"
        f"🆔 آیدی: {uid}\n"
        f"👤 نام: {query.from_user.full_name}\n"
        f"💳 کارت میویی: {card}\n"
        f"📊 مقدار: {amount:g} میلیون\n"
        f"💰 مبلغ: {total:,} تومان\n"
        f"💼 موجودی باقی‌مانده: {new_bal:,}"
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
        add_wallet(uid, amount)
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
