import os
import math
import asyncio
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

import asyncpg
from aiohttp import web
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ---------------------- تنظیمات ----------------------
TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1713081033"))
PAYMENT_CARD = os.environ.get("PAYMENT_CARD", "5047061673513814")
PAYMENT_CARD_HOLDER = os.environ.get("PAYMENT_CARD_HOLDER", "صادقی")
CHANNELS = ["@Channle_Meowie_Buy", "@Meow_Point_Free"]

PORT = int(os.environ.get("PORT", "10000"))
WEB_HOST = "0.0.0.0"

DEFAULT_LOW_RATE = 2500
DEFAULT_HIGH_RATE = 2100
REFERRAL_REWARD = 150_000
MIN_WITHDRAW_POINTS = 500_000
MIN_REFERRALS = 5

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("meow-point")

BUY_TIER, BUY_AMOUNT, SUPPORT_TEXT, TOPUP_RECEIPT, ADMIN_SEARCH = range(5)

MAIN_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("🛒 خرید میو پوینت"), KeyboardButton("👛 کیف پول")],
    [KeyboardButton("🎧 پشتیبانی"), KeyboardButton("👥 دعوت دوستان")],
    [KeyboardButton("❓ چطور اعتماد کنم"), KeyboardButton("📖 راهنما")],
    [KeyboardButton("📦 موجودی ما چقدره")],
], resize_keyboard=True)

ADMIN_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("📊 آمار"), KeyboardButton("👤 کاربران")],
    [KeyboardButton("➕ شارژ/➖ کسر کیف پول"), KeyboardButton("✉️ پیام به کاربر")],
    [KeyboardButton("💰 تغییر قیمت"), KeyboardButton("📣 پیام همگانی")],
    [KeyboardButton("🎁 عملیات همگانی"), KeyboardButton("🏠 منوی اصلی")],
], resize_keyboard=True)

# ---------------------- دیتابیس ----------------------
async def init_db(pool):
    await pool.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
        balance BIGINT NOT NULL DEFAULT 0, referral_code TEXT UNIQUE,
        referred_by BIGINT REFERENCES users(user_id),
        referrals_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS topups (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id), receipt_file_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS tickets (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id), message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id), points BIGINT NOT NULL, amount_toman BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
    CREATE TABLE IF NOT EXISTS withdrawals (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id), points BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
    """)
    defaults = {"low_rate": str(DEFAULT_LOW_RATE), "high_rate": str(DEFAULT_HIGH_RATE), "inventory": "0"}
    for key, value in defaults.items():
        await pool.execute("INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO NOTHING", key, value)

async def setting(context, key):
    row = await context.application.bot_data["pool"].fetchrow("SELECT value FROM settings WHERE key=$1", key)
    return row["value"] if row else "0"

async def set_setting(context, key, value):
    await context.application.bot_data["pool"].execute("""INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""", key, str(value))

async def ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    pool = context.application.bot_data["pool"]
    await pool.execute("""INSERT INTO users(user_id, username, first_name, referral_code) VALUES($1,$2,$3,$4) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name""", user.id, user.username, user.first_name, str(user.id))

async def is_admin(update):
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

async def joined_channels(context, user_id):
    for channel in CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked"): return False
        except Exception: return False
    return True

async def membership_gate(update, context):
    if await is_admin(update): return True
    ok = await joined_channels(context, update.effective_user.id)
    if not ok:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("عضویت در کانال اول", url="https://t.me/Channle_Meowie_Buy")],
            [InlineKeyboardButton("عضویت در کانال دوم", url="https://t.me/Meow_Point_Free")],
            [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")],
        ])
        await update.effective_message.reply_text("برای استفاده از ربات، ابتدا در هر دو کانال عضو شو و سپس «بررسی عضویت» را بزن.", reply_markup=keyboard)
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update, context)
    if context.args:
        try:
            referrer_id = int(context.args[0])
            uid = update.effective_user.id
            if referrer_id != uid:
                pool = context.application.bot_data["pool"]
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        me = await conn.fetchrow("SELECT referred_by FROM users WHERE user_id=$1 FOR UPDATE", uid)
                        ref = await conn.fetchrow("SELECT user_id FROM users WHERE user_id=$1 FOR UPDATE", referrer_id)
                        if me and ref and me["referred_by"] is None:
                            await conn.execute("UPDATE users SET referred_by=$1 WHERE user_id=$2", referrer_id, uid)
                            await conn.execute("UPDATE users SET referrals_count=referrals_count+1, balance=balance+$1 WHERE user_id=$2", REFERRAL_REWARD, referrer_id)
                            try: await context.bot.send_message(referrer_id, f"🎉 یک نفر با دعوت تو وارد شد؛ {REFERRAL_REWARD:,} میوپوینت دریافت کردی.")
                            except: pass
        except: pass
    if not await membership_gate(update, context): return
    await update.effective_message.reply_text(f"سلام {update.effective_user.first_name} 🌸\nبه ربات Meow Point خوش اومدی!", reply_markup=ADMIN_MENU if await is_admin(update) else MAIN_MENU)

async def check_join_callback(update, context):
    query = update.callback_query
    await query.answer()
    if await joined_channels(context, query.from_user.id):
        await query.edit_message_text("عضویتت تأیید شد. حالا /start رو بزن.")
    else: await query.answer("هنوز عضویت هر دو کانال تأیید نشده.", show_alert=True)

async def menu_guard(update, context):
    await ensure_user(update, context)
    return await membership_gate(update, context)

async def wallet(update, context):
    if not await menu_guard(update, context): return
    row = await context.application.bot_data["pool"].fetchrow("SELECT balance FROM users WHERE user_id=$1", update.effective_user.id)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 شارژ کیف پول (ارسال رسید)", callback_data="topup_start")]])
    await update.message.reply_text(f"👛 موجودی کیف پول: **{row['balance']:,}** میوپوینت", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def topup_start(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["awaiting_topup"] = True
    await q.message.reply_text("لطفاً فقط تصویر رسید پرداخت را بفرست.\nپس از بررسی، ادمین کیف پولت را شارژ می‌کند.")

async def receive_photo(update, context):
    if not await menu_guard(update, context): return
    if context.user_data.pop("awaiting_topup", False):
        photo = update.message.photo[-1]
        pool = context.application.bot_data["pool"]
        topup_id = await pool.fetchval("INSERT INTO topups(user_id,receipt_file_id) VALUES($1,$2) RETURNING id", update.effective_user.id, photo.file_id)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید و شارژ", callback_data=f"topup_ok:{topup_id}"), InlineKeyboardButton("❌ رد", callback_data=f"topup_no:{topup_id}")]])
        await context.bot.send_photo(ADMIN_ID, photo.file_id, caption=f"رسید شارژ #{topup_id}\nکاربر: {update.effective_user.id} (@{update.effective_user.username or '-'})", reply_markup=buttons)
        await update.message.reply_text("رسیدت برای ادمین ارسال شد. پس از بررسی نتیجه اعلام می‌شود.", reply_markup=MAIN_MENU)
        return
    await update.message.reply_text("برای ارسال رسید، ابتدا از بخش کیف پول گزینه شارژ را بزن.")

async def topup_decision(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID: await q.answer("دسترسی ندارید.", show_alert=True); return
    await q.answer()
    action, raw_id = q.data.split(":")
    topup_id = int(raw_id)
    pool = context.application.bot_data["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM topups WHERE id=$1 FOR UPDATE", topup_id)
            if not row or row["status"] != "pending": await q.edit_message_caption("این درخواست قبلاً بررسی شده یا وجود ندارد."); return
            if action == "topup_ok":
                await conn.execute("UPDATE users SET balance=balance+1000000 WHERE user_id=$1", row["user_id"])
                await conn.execute("UPDATE topups SET status='approved' WHERE id=$1", topup_id)
                msg = "رسیدت تأیید شد و ۱,۰۰۰,۰۰۰ میوپوینت به کیف پولت اضافه شد."
            else:
                await conn.execute("UPDATE topups SET status='rejected' WHERE id=$1", topup_id)
                msg = "رسید شارژت تأیید نشد. برای پیگیری با پشتیبانی تماس بگیر."
    await context.bot.send_message(row["user_id"], msg)
    await q.edit_message_caption(f"درخواست #{topup_id} بررسی شد: {action}")

async def buy_start(update, context):
    if not await menu_guard(update, context): return ConversationHandler.END
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("بالای ۱۰۰ میلیون", callback_data="tier:high")], [InlineKeyboardButton("پایین ۱۰۰ میلیون", callback_data="tier:low")]])
    await update.message.reply_text("نوع سفارش رو انتخاب کن:", reply_markup=kb)
    return BUY_TIER

async def buy_tier(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["buy_tier"] = q.data.split(":")[1]
    await q.message.reply_text("مقدار میوپوینت را به عدد وارد کن (مثلاً 150000000):")
    return BUY_AMOUNT

async def buy_amount(update, context):
    try:
        points = int(update.message.text.replace(",", "").replace("٬", "").strip())
        if points <= 0 or points > 10_000_000_000: raise ValueError
    except:
        await update.message.reply_text("عدد معتبر وارد کن؛ مثلاً 150000000.")
        return BUY_AMOUNT
    tier = context.user_data.get("buy_tier", "low")
    rate = int(await setting(context, "high_rate" if tier == "high" else "low_rate"))
    price = math.ceil(Decimal(points) / Decimal(1_000_000) * rate)
    context.user_data["buy_points"] = points
    context.user_data["buy_price"] = price
    await update.message.reply_text(
        f"🧾 سفارش: {points:,} میوپوینت\n💰 مبلغ: {price:,} تومان\nشماره کارت میویی: {PAYMENT_CARD}\nبه نام: {PAYMENT_CARD_HOLDER}\n\nاگر موجودی کیف پولت کافی است، از دکمه زیر پرداخت کن.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("پرداخت با کیف پول", callback_data="wallet_pay")]])
    )
    return ConversationHandler.END

async def wallet_pay(update, context):
    q = update.callback_query
    await q.answer()
    points = context.user_data.get("buy_points")
    price = context.user_data.get("buy_price")
    if not points or not price: await q.message.reply_text("سفارشی برای پرداخت پیدا نشد. دوباره خرید را شروع کن."); return
    pool = context.application.bot_data["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", q.from_user.id)
            if not user or user["balance"] < price: await q.message.reply_text("موجودی کیف پول کافی نیست. ابتدا کیف پولت را شارژ کن."); return
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", price, q.from_user.id)
            order_id = await conn.fetchval("INSERT INTO orders(user_id,points,amount_toman) VALUES($1,$2,$3) RETURNING id", q.from_user.id, points, price)
    context.user_data.pop("buy_points", None)
    context.user_data.pop("buy_price", None)
    await q.message.reply_text(f"پرداخت سفارش #{order_id} ثبت شد. برای تحویل با پشتیبانی هماهنگ کن.", reply_markup=MAIN_MENU)
    await context.bot.send_message(ADMIN_ID, f"🛒 سفارش جدید #{order_id}\nکاربر: {q.from_user.id}\nمقدار: {points:,}\nمبلغ: {price:,} تومان")

async def support_start(update, context):
    if not await menu_guard(update, context): return ConversationHandler.END
    await update.message.reply_text("پیامت رو برای پشتیبانی بنویس:")
    return SUPPORT_TEXT

async def support_text(update, context):
    pool = context.application.bot_data["pool"]
    ticket_id = await pool.fetchval("INSERT INTO tickets(user_id,message) VALUES($1,$2) RETURNING id", update.effective_user.id, update.message.text[:4000])
    await context.bot.send_message(ADMIN_ID, f"🎧 تیکت #{ticket_id}\nکاربر: {update.effective_user.id}\n\n{update.message.text[:3500]}")
    await update.message.reply_text("تیکتت برای پشتیبانی ارسال شد.", reply_markup=MAIN_MENU)
    return ConversationHandler.END

async def referrals(update, context):
    if not await menu_guard(update, context): return
    user = update.effective_user
    me = await context.bot.get_me()
    row = await context.application.bot_data["pool"].fetchrow("SELECT referrals_count,balance FROM users WHERE user_id=$1", user.id)
    link = f"https://t.me/{me.username}?start={user.id}"
    await update.message.reply_text(f"👥 لینک دعوت اختصاصی تو:\n{link}\n\nتعداد دعوت‌ها: {row['referrals_count']}\nموجودی: {row['balance']:,} میوپوینت\nپاداش هر دعوت: {REFERRAL_REWARD:,}\nحداقل برداشت: {MIN_WITHDRAW_POINTS:,} میوپوینت\nحداقل تعداد دعوت: {MIN_REFERRALS}\n\nبرای ثبت درخواست برداشت، عبارت «درخواست برداشت» را بفرست.")

async def withdrawal_request(update, context):
    pool = context.application.bot_data["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1 FOR UPDATE", update.effective_user.id)
            if not row or row["referrals_count"] < MIN_REFERRALS or row["balance"] < MIN_WITHDRAW_POINTS:
                await update.message.reply_text(f"برای برداشت باید حداقل {MIN_REFERRALS} دعوت موفق و {MIN_WITHDRAW_POINTS:,} میوپوینت موجودی داشته باشی.")
                return
            await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", MIN_WITHDRAW_POINTS, update.effective_user.id)
            wid = await conn.fetchval("INSERT INTO withdrawals(user_id,points) VALUES($1,$2) RETURNING id", update.effective_user.id, MIN_WITHDRAW_POINTS)
    await context.bot.send_message(ADMIN_ID, f"درخواست برداشت #{wid}\nکاربر: {update.effective_user.id}\nمقدار: {MIN_WITHDRAW_POINTS:,}")
    await update.message.reply_text("درخواست برداشت ثبت شد و پس از بررسی ادمین پیگیری می‌شود.")

async def trust(update, context):
    if await menu_guard(update, context): await update.message.reply_text("برای اطمینان، قبل از پرداخت قوانین و جزئیات سفارش را بررسی کن. رسیدها دستی بررسی می‌شوند و می‌توانی از بخش پشتیبانی پیگیری کنی.")

async def help_text(update, context):
    if await menu_guard(update, context): await update.message.reply_text("راهنما:\n۱) برای خرید، مقدار میوپوینت را وارد کن.\n۲) مبلغ به‌صورت خودکار محاسبه می‌شود.\n۳) برای شارژ کیف پول رسید پرداخت را ارسال کن.\n۴) از لینک دعوت برای دریافت پاداش استفاده کن.")

async def inventory(update, context):
    if await menu_guard(update, context):
        value = int(await setting(context, "inventory"))
        await update.message.reply_text(f"📦 موجودی فعلی ما: {value:,} میوپوینت")

async def admin_entry(update, context):
    if not await is_admin(update): await update.message.reply_text("دسترسی ندارید."); return
    await update.message.reply_text("پنل مدیریت:", reply_markup=ADMIN_MENU)

async def admin_stats(update, context):
    if not await is_admin(update): return
    pool = context.application.bot_data["pool"]
    users, balances, orders, pending = await asyncio.gather(
        pool.fetchval("SELECT COUNT(*) FROM users"),
        pool.fetchval("SELECT COALESCE(SUM(balance),0) FROM users"),
        pool.fetchval("SELECT COUNT(*) FROM orders"),
        pool.fetchval("SELECT COUNT(*) FROM topups WHERE status='pending'")
    )
    await update.message.reply_text(f"📊 آمار ربات\nکاربران: {users:,}\nمجموع موجودی: {balances:,}\nسفارش‌ها: {orders:,}\nرسیدهای در انتظار: {pending:,}")

async def admin_users(update, context):
    if not await is_admin(update): return
    await update.message.reply_text("برای جستجو، آیدی عددی یا بخشی از نام کاربری را بفرست:")
    return ADMIN_SEARCH

async def admin_search(update, context):
    if not await is_admin(update): return ConversationHandler.END
    term = update.message.text.strip()
    pool = context.application.bot_data["pool"]
    if term.isdigit():
        rows = await pool.fetch("SELECT user_id,username,first_name,balance FROM users WHERE user_id=$1", int(term))
    else:
        rows = await pool.fetch("""SELECT user_id,username,first_name,balance FROM users WHERE COALESCE(username,'') ILIKE $1 OR COALESCE(first_name,'') ILIKE $1 ORDER BY created_at DESC LIMIT 10""", f"%{term}%")
    if not rows: await update.message.reply_text("کاربری پیدا نشد."); return ConversationHandler.END
    for r in rows:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ شارژ ۱۰۰هزار", callback_data=f"adm_add:{r['user_id']}:100000"), InlineKeyboardButton("➖ کسر ۱۰۰هزار", callback_data=f"adm_sub:{r['user_id']}:100000")],[InlineKeyboardButton("✉️ پیام", callback_data=f"adm_msg:{r['user_id']}")]])
        await update.message.reply_text(f"ID: {r['user_id']}\n@{r['username'] or '-'} | {r['first_name'] or '-'}\nموجودی: {r['balance']:,}", reply_markup=kb)
    return ConversationHandler.END

async def admin_balance_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID: await q.answer("دسترسی ندارید.", show_alert=True); return
    await q.answer()
    action, uid, amount = q.data.split(":")
    uid, amount = int(uid), int(amount)
    delta = amount if action == "adm_add" else -amount
    await context.application.bot_data["pool"].execute("UPDATE users SET balance=GREATEST(0,balance+$1) WHERE user_id=$2", delta, uid)
    await q.message.reply_text("موجودی کاربر به‌روزرسانی شد.")

async def admin_message_callback(update, context):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID: await q.answer("دسترسی ندارید.", show_alert=True); return
    await q.answer()
    context.user_data["admin_message_to"] = int(q.data.split(":")[1])
    await q.message.reply_text("متن پیام برای کاربر را بفرست.")

async def admin_message_text(update, context):
    if not await is_admin(update) or "admin_message_to" not in context.user_data: return
    uid = context.user_data.pop("admin_message_to")
    try:
        await context.bot.send_message(uid, update.message.text)
        await update.message.reply_text("پیام ارسال شد.")
    except Exception as exc: await update.message.reply_text(f"ارسال ناموفق بود: {exc}")

async def admin_price(update, context):
    if not await is_admin(update): return
    await update.message.reply_text(f"قیمت فعلی هر یک میلیون:\nبالای ۱۰۰ میلیون: {await setting(context,'high_rate')} تومان\nپایین ۱۰۰ میلیون: {await setting(context,'low_rate')} تومان\n\nبرای تغییر بنویس: high 2100 یا low 2500")

async def admin_price_text(update, context):
    if not await is_admin(update): return
    parts = update.message.text.strip().split()
    if len(parts) == 2 and parts[0] in ("high", "low") and parts[1].isdigit():
        amount = int(parts[1])
        if 1 <= amount <= 100_000_000:
            await set_setting(context, "high_rate" if parts[0] == "high" else "low_rate", amount)
            await update.message.reply_text("قیمت تغییر کرد.")
            return
    await update.message.reply_text("فرمت درست: high 2100 یا low 2500")

async def admin_broadcast(update, context):
    if not await is_admin(update): return
    context.user_data["broadcast_mode"] = True
    await update.message.reply_text("متن پیام همگانی را بفرست.")

async def admin_bulk(update, context):
    if not await is_admin(update): return
    context.user_data["bulk_mode"] = True
    await update.message.reply_text("برای عملیات همگانی بنویس: add 50000 یا sub 50000")

async def admin_special_text(update, context):
    if not await is_admin(update): return
    pool = context.application.bot_data["pool"]
    if context.user_data.pop("broadcast_mode", False):
        ids = await pool.fetch("SELECT user_id FROM users")
        sent = 0
        for row in ids:
            try: await context.bot.send_message(row["user_id"], update.message.text); sent += 1
            except: pass
        await update.message.reply_text(f"پیام برای {sent} کاربر ارسال شد.")
        return
    if context.user_data.pop("bulk_mode", False):
        parts = update.message.text.split()
        if len(parts) == 2 and parts[0] in ("add", "sub") and parts[1].isdigit():
            amount = int(parts[1])
            if amount > 0:
                delta = amount if parts[0] == "add" else -amount
                await pool.execute("UPDATE users SET balance=GREATEST(0,balance+$1)", delta)
                await update.message.reply_text("عملیات همگانی انجام شد.")
                return
        await update.message.reply_text("فرمت: add 50000 یا sub 50000")

async def text_router(update, context):
    text = update.message.text.strip()
    if text == "👛 کیف پول": await wallet(update, context)
    elif text == "🎧 پشتیبانی": return await support_start(update, context)
    elif text == "👥 دعوت دوستان": await referrals(update, context)
    elif text == "❓ چطور اعتماد کنم": await trust(update, context)
    elif text == "📖 راهنما": await help_text(update, context)
    elif text == "📦 موجودی ما چقدره": await inventory(update, context)
    elif text == "🛒 خرید میو پوینت": return await buy_start(update, context)
    elif text == "📊 آمار" and await is_admin(update): await admin_stats(update, context)
    elif text == "👤 کاربران" and await is_admin(update): return await admin_users(update, context)
    elif text == "💰 تغییر قیمت" and await is_admin(update): await admin_price(update, context)
    elif text == "📣 پیام همگانی" and await is_admin(update): await admin_broadcast(update, context)
    elif text == "🎁 عملیات همگانی" and await is_admin(update): await admin_bulk(update, context)
    elif text == "➕ شارژ/➖ کسر کیف پول" and await is_admin(update): return await admin_users(update, context)
    elif text == "🏠 منوی اصلی": await update.message.reply_text("منوی اصلی:", reply_markup=MAIN_MENU)
    elif text == "درخواست برداشت": await withdrawal_request(update, context)
    elif await is_admin(update) and (context.user_data.get("admin_message_to") is not None or context.user_data.get("broadcast_mode") or context.user_data.get("bulk_mode")):
        await admin_special_text(update, context)
    elif await is_admin(update) and len(text.split()) == 2 and text.split()[0] in ("high", "low"):
        await admin_price_text(update, context)
    else:
        if not await is_admin(update): await update.message.reply_text("از دکمه‌های منو استفاده کن.")

async def error_handler(update, context):
    log.exception("Unhandled error", exc_info=context.error)
    if update and update.effective_message: await update.effective_message.reply_text("خطایی رخ داد. لطفاً کمی بعد دوباره تلاش کن.")

async def health(request): return web.Response(text="Meow Point bot is alive")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, PORT)
    await site.start()
    return runner

async def post_init(app):
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, command_timeout=30)
    app.bot_data["pool"] = pool
    await init_db(pool)
    app.bot_data["web_runner"] = await start_webserver()

async def post_shutdown(app):
    runner = app.bot_data.get("web_runner")
    if runner: await runner.cleanup()
    pool = app.bot_data.get("pool")
    if pool: await pool.close()

def main():
    if not TOKEN: raise RuntimeError("Set TOKEN in environment variables.")
    if ADMIN_ID <= 0: raise RuntimeError("Set ADMIN_ID in environment variables.")
    application = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    buy_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🛒 خرید میو پوینت$"), buy_start)], states={BUY_TIER: [CallbackQueryHandler(buy_tier, pattern=r"^tier:(high|low)$")], BUY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_amount)]}, fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)])
    support_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🎧 پشتیبانی$"), support_start)], states={SUPPORT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_text)]}, fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)])
    users_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex("^👤 کاربران$|^➕ شارژ/➖ کسر کیف پول$"), admin_users)], states={ADMIN_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search)]}, fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)])

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_entry))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(topup_start, pattern="^topup_start$"))
    application.add_handler(CallbackQueryHandler(topup_decision, pattern=r"^topup_(ok|no):\d+$"))
    application.add_handler(CallbackQueryHandler(wallet_pay, pattern="^wallet_pay$"))
    application.add_handler(CallbackQueryHandler(admin_balance_callback, pattern=r"^adm_(add|sub):"))
    application.add_handler(CallbackQueryHandler(admin_message_callback, pattern=r"^adm_msg:"))
    application.add_handler(buy_conv)
    application.add_handler(support_conv)
    application.add_handler(users_conv)
    application.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_error_handler(error_handler)

    # FIX for Python 3.14 event loop error
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
