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
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
ADMIN_ID = 1713081033
CARD_NUMBER = "5047061673513814"
CARD_NAME = "صادقی"

REQUIRED_CHANNELS = [
    {"id": "@Channle_Meowie_Buy", "link": "https://t.me/Channle_Meowie_Buy"},
    {"id": "@Meow_Point_Free", "link": "https://t.me/Meow_Point_Free"},
]

logging.basicConfig(level=logging.INFO)
connection_pool = psycopg2.pool.SimpleConnectionPool(1, 20, dsn=DATABASE_URL)
login_clients = {}
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

def init_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            diamonds BIGINT DEFAULT 10,
            group_id TEXT,
            phone_number TEXT,
            session_string TEXT,
            is_active BOOLEAN DEFAULT FALSE,
            ref_code VARCHAR(10) UNIQUE,
            referred_by BIGINT,
            referrals_count INTEGER DEFAULT 0,
            full_name TEXT,
            joined_at TIMESTAMP DEFAULT NOW(),
            miner_status BOOLEAN DEFAULT FALSE,
            miner_token TEXT DEFAULT 'TON',
            auto_meow_status BOOLEAN DEFAULT FALSE,
            factory_status BOOLEAN DEFAULT FALSE,
            factory_product TEXT DEFAULT 'تکنولوژی',
            warehouse_status BOOLEAN DEFAULT FALSE,
            cat_status BOOLEAN DEFAULT FALSE,
            fish_status BOOLEAN DEFAULT FALSE
        )""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS diamonds BIGINT DEFAULT 10""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS session_string TEXT""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS miner_status BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS miner_token TEXT DEFAULT 'TON'""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_meow_status BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS factory_status BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS factory_product TEXT DEFAULT 'تکنولوژی'""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS warehouse_status BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS cat_status BOOLEAN DEFAULT FALSE""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS fish_status BOOLEAN DEFAULT FALSE""",
        """CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""",
        """CREATE TABLE IF NOT EXISTS pending_charges (
            id SERIAL PRIMARY KEY, user_id BIGINT, amount BIGINT, photo_id TEXT, card_photo_id TEXT, status TEXT DEFAULT 'pending'
        )"""
    ]
    for q in queries:
        try:
            db_execute(q)
        except Exception as e:
            logging.error(f"Init DB error: {e}")

init_db()
def gen_ref_code():
    while True:
        code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        r = db_execute("SELECT 1 FROM users WHERE ref_code=%s", (code,), fetch=True)
        if not r:
            return code

def fa_to_en(text):
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

def set_state(uid, state, data=""):
    db_execute("""INSERT INTO states (user_id, state, data) VALUES (%s, %s, %s)
                  ON CONFLICT (user_id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data""", (uid, state, data))

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
            if member.status in ["left", "kicked"]:
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined
    def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔌 فعال‌سازی سلف")],
        [KeyboardButton("⚙️ سلف"), KeyboardButton("💎 خرید الماس")],
        [KeyboardButton("🎁 الماس رایگان")]
    ], resize_keyboard=True)

def self_menu_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🌐 تنظیم گپ")],
        [KeyboardButton("⛏ ماینر"), KeyboardButton("🏭 کارخونه")],
        [KeyboardButton("🐈 پیشی"), KeyboardButton("🐟 ماهی خودکار")],
        [KeyboardButton("💬 میو خودکار")],
        [KeyboardButton("🔙 بازگشت")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True)

def join_kb(not_joined):
    buttons = [[InlineKeyboardButton(f"📢 عضویت در {ch['id']}", url=ch["link"])] for ch in not_joined]
    buttons.append([InlineKeyboardButton("✅ تلاش مجدد", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)
    async def process_start_logic(update, context, uid, user):
    if not db_execute("SELECT 1 FROM users WHERE user_id=%s", (uid,), fetch=True):
        db_execute("INSERT INTO users (user_id, ref_code, full_name) VALUES (%s, %s, %s)", (uid, gen_ref_code(), user.full_name))
    clear_state(uid)
    diamonds = db_execute("SELECT diamonds FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
    await context.bot.send_message(uid, f"به ربات خوش آمدید! 👋\n💎 موجودی الماس شما: {diamonds}\n\nلطفا یک گزینه را انتخاب کنید:", reply_markup=main_kb())

async def start(update, context):
    uid = update.effective_user.id
    not_joined = await check_membership(context, uid)
    if not_joined:
        await update.message.reply_text("⚠️ برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شوید:", reply_markup=join_kb(not_joined))
        return
    await process_start_logic(update, context, uid, update.effective_user)

async def do_get_phone(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=main_kb())
    phone = update.message.text.strip()
    await update.message.reply_text("در حال اتصال به تلگرام... لطفا صبر کنید.")
    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            login_clients[uid] = client
            set_state(uid, "WAIT_OTP", phone)
            await update.message.reply_text("کد ورود به تلگرام شما ارسال شد. لطفا کد را وارد کنید:", reply_markup=cancel_kb())
        else:
            await client.disconnect()
            await update.message.reply_text("این شماره قبلا ثبت شده است.")
    except Exception as e:
        logging.error(f"Login error: {e}")
        await update.message.reply_text("خطا در ارسال کد. آیا شماره را درست وارد کردید؟ دوباره تلاش کنید.")

async def do_get_otp(update, context, uid):
    if update.message.text == "🔙 بازگشت":
        if uid in login_clients:
            await login_clients[uid].disconnect()
            del login_clients[uid]
        clear_state(uid)
        return await update.message.reply_text("لغو شد.", reply_markup=main_kb())
    otp = update.message.text.strip()
    state, phone = get_state(uid)
    client = login_clients.get(uid)
    if not client:
        clear_state(uid)
        return await update.message.reply_text("جلسه منقضی شده. دوباره شماره را وارد کنید.")
    try:
        await client.sign_in(phone, otp)
        session_string = client.session.save()
        db_execute("UPDATE users SET phone_number=%s, session_string=%s, is_active=TRUE WHERE user_id=%s", (phone, session_string, uid))
        await client.disconnect()
        del login_clients[uid]
        clear_state(uid)
        await update.message.reply_text("✅ اکانت شما با موفقیت متصل شد!", reply_markup=main_kb())
    except SessionPasswordNeededError:
        await update.message.reply_text("❌ اکانت شما رمز دو مرحله‌ای (2FA) دارد. لطفا رمز خود را وارد کنید:")
        set_state(uid, "WAIT_2FA", phone)
    except Exception as e:
        logging.error(f"OTP error: {e}")
        await update.message.reply_text("❌ کد ورود اشتباه است. دوباره تلاش کنید:")

async def do_get_2fa(update, context, uid):
    password = update.message.text.strip()
    state, phone = get_state(uid)
    client = login_clients.get(uid)
    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        db_execute("UPDATE users SET phone_number=%s, session_string=%s, is_active=TRUE WHERE user_id=%s", (phone, session_string, uid))
        await client.disconnect()
        del login_clients[uid]
        clear_state(uid)
        await update.message.reply_text("✅ اکانت شما با موفقیت متصل شد!", reply_markup=main_kb())
    except Exception as e:
        await update.message.reply_text("❌ رمز اشتباه است. دوباره تلاش کنید:")
        async def do_set_group(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=self_menu_kb())
    gid = update.message.text.strip()
    db_execute("UPDATE users SET group_id=%s WHERE user_id=%s", (gid, uid))
    clear_state(uid)
    await update.message.reply_text("✅ گپ با موفقیت تنظیم شد.", reply_markup=self_menu_kb())

async def do_set_miner_token(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=self_menu_kb())
    token = update.message.text.strip().upper()
    db_execute("UPDATE users SET miner_token=%s WHERE user_id=%s", (token, uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ توکن ماینر روی {token} تنظیم شد.", reply_markup=self_menu_kb())

async def do_set_factory_product(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=self_menu_kb())
    prod = update.message.text.strip()
    db_execute("UPDATE users SET factory_product=%s WHERE user_id=%s", (prod, uid))
    clear_state(uid)
    await update.message.reply_text(f"✅ محصول کارخونه روی {prod} تنظیم شد.", reply_markup=self_menu_kb())

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    not_joined = await check_membership(context, uid)
    if not_joined:
        await update.message.reply_text("⚠️ ابتدا در کانال‌ها عضو شوید:", reply_markup=join_kb(not_joined))
        return

    state, data = get_state(uid)
    if state == "WAIT_GROUP_ID": return await do_set_group(update, context, uid)
    if state == "WAIT_PHONE": return await do_get_phone(update, context, uid)
    if state == "WAIT_OTP": return await do_get_otp(update, context, uid)
    if state == "WAIT_MINER_TOKEN": return await do_set_miner_token(update, context, uid)
    if state == "WAIT_FACTORY_PRODUCT": return await do_set_factory_product(update, context, uid)

    if text == "🔌 فعال‌سازی سلف":
        set_state(uid, "WAIT_PHONE")
        await update.message.reply_text("لطفا شماره تلفن خود را با کد کشور وارد کنید (مثلاً +989123456789):", reply_markup=cancel_kb())
    elif text == "⚙️ سلف":
        await update.message.reply_text("به تنظیمات سلف خوش آمدید. یک گزینه را انتخاب کنید:", reply_markup=self_menu_kb())
    elif text == "💎 خرید الماس":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💵 خرید با میو", callback_data="buy_mio")], [InlineKeyboardButton("💳 خرید با پول", callback_data="buy_money")]])
        await update.message.reply_text("روش خرید را انتخاب کنید:", reply_markup=kb)
    elif text == "🎁 الماس رایگان":
        ref_code = db_execute("SELECT ref_code FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        link = f"https://t.me/{context.bot.username}?start={ref_code}"
        await update.message.reply_text(f"🎁 لینک رفرال شما:\n`{link}`\n\nبا هر نفر 50 الماس بگیرید.", parse_mode="Markdown")
    elif text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb())
    elif text == "🌐 تنظیم گپ":
        set_state(uid, "WAIT_GROUP_ID")
        await update.message.reply_text("لطفا آیدی عددی یا لینک گپ مورد نظر را ارسال کنید:", reply_markup=cancel_kb())
    elif text == "⛏ ماینر":
        status = db_execute("SELECT miner_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("خاموش" if status else "روشن", callback_data=f"toggle_miner_{'0' if status else '1'}")], [InlineKeyboardButton("⚙️ انتخاب توکن", callback_data="select_miner_token")]])
        await update.message.reply_text(f"وضعیت ماینر: {'روشن' if status else 'خاموش'}", reply_markup=kb)
    elif text == "🏭 کارخونه":
        status = db_execute("SELECT factory_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        wh = db_execute("SELECT warehouse_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("خاموش" if status else "روشن", callback_data=f"toggle_factory_{'0' if status else '1'}")],[InlineKeyboardButton("⚙️ انتخاب تولید", callback_data="select_factory_product")],[InlineKeyboardButton(f"انبار: {'روشن' if wh else 'خاموش'}", callback_data=f"toggle_warehouse_{'0' if wh else '1'}")]])
        await update.message.reply_text(f"وضعیت کارخونه: {'روشن' if status else 'خاموش'}", reply_markup=kb)
    elif text == "🐈 پیشی":
        status = db_execute("SELECT cat_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("خاموش" if status else "روشن", callback_data=f"toggle_cat_{'0' if status else '1'}")]])
        await update.message.reply_text(f"وضعیت پیشی: {'روشن' if status else 'خاموش'}", reply_markup=kb)
    elif text == "🐟 ماهی خودکار":
        status = db_execute("SELECT fish_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("خاموش" if status else "روشن", callback_data=f"toggle_fish_{'0' if status else '1'}")]])
        await update.message.reply_text(f"وضعیت ماهی خودکار: {'روشن' if status else 'خاموش'}", reply_markup=kb)
    elif text == "💬 میو خودکار":
        status = db_execute("SELECT auto_meow_status FROM users WHERE user_id=%s", (uid,), fetch=True)[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("خاموش" if status else "روشن", callback_data=f"toggle_meow_{'0' if status else '1'}")]])
        await update.message.reply_text(f"وضعیت میو خودکار: {'روشن' if status else 'خاموش'}", reply_markup=kb)
    else:
        await update.message.reply_text("لطفا از دکمه‌ها استفاده کنید:", reply_markup=main_kb())
        async def do_buy_mio_amount(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=main_kb())
    try:
        amount = int(fa_to_en(update.message.text))
        total_mio = amount * 500000
        set_state(uid, "WAIT_MIO_CARD", f"{amount}|{total_mio}")
        await update.message.reply_text(f"✅ مقدار: {amount} الماس\n💰 قیمت: {total_mio:,} میو پوینت\n\nشماره کارت میویی خود را وارد کنید:")
    except: await update.message.reply_text("عدد معتبر وارد کنید.")

async def do_buy_mio_card(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=main_kb())
    card = update.message.text.strip()
    state, data = get_state(uid)
    amount, total_mio = data.split("|")
    await context.bot.send_message(ADMIN_ID, f"💎 درخواست خرید الماس (با میو)\nکاربر: {uid}\nالماس: {amount}\nمبلغ: {total_mio} میو\nکارت: {card}")
    clear_state(uid)
    await update.message.reply_text("✅ درخواست شما ثبت شد.", reply_markup=main_kb())

async def do_buy_money_amount(update, context, uid):
    if update.message.text == "🔙 بازگشت": clear_state(uid); return await update.message.reply_text("لغو شد.", reply_markup=main_kb())
    try:
        amount = int(fa_to_en(update.message.text))
        total_money = amount * 100000
        set_state(uid, "WAIT_MONEY_RECEIPT", f"{amount}|{total_money}")
        await update.message.reply_text(f"✅ مقدار: {amount} الماس\n💰 قیمت: {total_money:,} تومان\n\nلطفا مبلغ را به کارت زیر واریز کنید:\n`{CARD_NUMBER}`\nبنام {CARD_NAME}\n\nسپس عکس رسید را ارسال کنید.", parse_mode="Markdown")
    except: await update.message.reply_text("عدد معتبر وارد کنید.")

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
    elif data == "buy_mio":
        await query.answer()
        set_state(uid, "WAIT_DIAMOND_MIO")
        await query.edit_message_text("💎 چند الماس می‌خواهید؟\n(هر ۱ الماس = 500,000 میو پوینت)")
    elif data == "buy_money":
        await query.answer()
        set_state(uid, "WAIT_DIAMOND_MONEY")
        await query.edit_message_text("💎 چند الماس می‌خواهید؟\n(هر ۱ الماس = 100,000 تومان)")
    elif data.startswith("toggle_"):
        await query.answer()
        parts = data.split("_")
        field = parts[1] + "_status"
        val = True if parts[-1] == '1' else False
        db_execute(f"UPDATE users SET {field}=%s WHERE user_id=%s", (val, uid))
        await query.edit_message_text(f"وضعیت به {'روشن' if val else 'خاموش'} تغییر یافت.")
    elif data == "select_miner_token":
        await query.answer()
        set_state(uid, "WAIT_MINER_TOKEN")
        await query.edit_message_text("لطفا یکی از این توکن‌ها را بنویسید:\nUSDT, TON, BTC, ETH, SOL")
    elif data == "select_factory_product":
        await query.answer()
        set_state(uid, "WAIT_FACTORY_PRODUCT")
        await query.edit_message_text("لطفا یکی از این محصولات را بنویسید:\nابنبات, کیک, تکنولوژی, خودرو, هواپیما, تکنولوژی پیشرفته")
    else:
        await query.answer()

async def on_photo(update, context):
    uid = update.effective_user.id
    state, data = get_state(uid)
    if state == "WAIT_MONEY_RECEIPT":
        photo_id = update.message.photo[-1].file_id
        amount, total_money = data.split("|")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید", callback_data=f"approve_diamond_{uid}_{amount}"), InlineKeyboardButton("❌ رد", callback_data="reject")]])
        await context.bot.send_photo(ADMIN_ID, photo=photo_id, caption=f"💳 رسید خرید الماس\nکاربر: {uid}\nالماس: {amount}\nمبلغ: {total_money}", reply_markup=kb)
        await update.message.reply_text("✅ رسید شما برای بررسی ارسال شد.")
        clear_state(uid)

async def approve_diamond_callback(update, context):
    query = update.callback_query
    data = query.data
    if data.startswith("approve_diamond_"):
        _, _, uid, amount = data.split("_")
        uid, amount = int(uid), int(amount)
        db_execute("UPDATE users SET diamonds = diamonds + %s WHERE user_id=%s", (amount, uid))
        await query.edit_message_caption("✅ تایید شد و الماس اضافه گردید.")
        await context.bot.send_message(uid, f"🎉 {amount} الماس به حساب شما اضافه شد!")
    elif data == "reject":
        await query.edit_message_caption("❌ رد شد.")
    await query.answer()
    app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(on_callback, pattern="^(check_join|buy_mio|buy_money|toggle_|select_)"))
app.add_handler(CallbackQueryHandler(approve_diamond_callback, pattern="^(approve_diamond_|reject)"))
app.add_handler(MessageHandler(filters.PHOTO, on_photo))

async def state_router(update, context):
    uid = update.effective_user.id
    state, data = get_state(uid)
    if state == "WAIT_DIAMOND_MIO": return await do_buy_mio_amount(update, context, uid)
    if state == "WAIT_MIO_CARD": return await do_buy_mio_card(update, context, uid)
    if state == "WAIT_DIAMOND_MONEY": return await do_buy_money_amount(update, context, uid)
    if state == "WAIT_2FA": return await do_get_2fa(update, context, uid)
    return await menu_router(update, context)

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, state_router))

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
