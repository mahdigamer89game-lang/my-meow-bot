
import os
import re
import asyncio
import logging
import secrets
from functools import partial

import psycopg2
from psycopg2.extras import RealDictCursor

from aiohttp import web

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_NAME = os.getenv("CARD_NAME", "")

CHANNELS = [
    x.strip() for x in
    os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()
]

SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")

REFERRAL_REWARD = 150000
REFERRAL_LIMIT = 5

if not all([TOKEN, DATABASE_URL, ADMIN_ID, CARD_NUMBER, CARD_NAME]):
    raise RuntimeError("Missing required environment variables")

if len(CHANNELS) != 2:
    raise RuntimeError("Exactly two REQUIRED_CHANNELS must be configured")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ================= DATABASE =================

def db_connect():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=15,
    )


def execute(sql, params=(), fetch=False, one=False):
    conn = db_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = None

                if fetch:
                    result = cur.fetchone() if one else cur.fetchall()

                return result
    finally:
        conn.close()


def init_db():
    conn = db_connect()

    try:
        with conn:
            with conn.cursor() as cur:

                cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
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
                );
                """)

                cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet BIGINT DEFAULT 0;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code VARCHAR(10) UNIQUE;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS referrals_count INTEGER DEFAULT 0;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS total_reward BIGINT DEFAULT 0;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP DEFAULT NOW();
                ALTER TABLE users ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_charges (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount BIGINT,
                    photo_id TEXT,
                    card_photo_id TEXT,
                    status TEXT DEFAULT 'pending'
                );
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """)

                cur.execute("""
                INSERT INTO settings (key, value)
                VALUES ('price_high', '2100')
                ON CONFLICT (key) DO NOTHING;
                """)

                cur.execute("""
                INSERT INTO settings (key, value)
                VALUES ('price_low', '2500')
                ON CONFLICT (key) DO NOTHING;
                """)

                cur.execute("""
                INSERT INTO settings (key, value)
                VALUES ('threshold', '100')
                ON CONFLICT (key) DO NOTHING;
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS states (
                    user_id BIGINT PRIMARY KEY,
                    state TEXT,
                    data TEXT
                );
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS withdraw_requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount BIGINT,
                    card TEXT,
                    status TEXT DEFAULT 'pending'
                );
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount BIGINT,
                    type TEXT,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """)

                cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_refs (
                    user_id BIGINT PRIMARY KEY,
                    ref_code VARCHAR(20)
                );
                """)

                # Extra migration for one-time referral rewards
                cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS referral_reward_claimed
                BOOLEAN DEFAULT FALSE;
                """)

    finally:
        conn.close()


# ================= ASYNC DATABASE =================

async def sql(sql_text, params=(), fetch=False, one=False):
    return await asyncio.to_thread(
        execute, sql_text, params, fetch, one
    )


# ================= HELPERS =================

def is_admin(user_id):
    return user_id == ADMIN_ID


def money(value):
    return f"{int(value):,}"


async def get_state(user_id):
    row = await sql(
        "SELECT state, data FROM states WHERE user_id=%s",
        (user_id,),
        fetch=True,
        one=True,
    )
    return row


async def set_state(user_id, state, data=""):
    await sql("""
        INSERT INTO states(user_id,state,data)
        VALUES(%s,%s,%s)
        ON CONFLICT(user_id)
        DO UPDATE SET state=EXCLUDED.state,data=EXCLUDED.data
    """, (user_id, state, str(data)))


async def clear_state(user_id):
    await sql("DELETE FROM states WHERE user_id=%s", (user_id,))


async def log_transaction(user_id, amount, kind, description):
    await sql("""
        INSERT INTO transactions(user_id,amount,type,description)
        VALUES(%s,%s,%s,%s)
    """, (user_id, amount, kind, description))


async def get_setting(key):
    row = await sql(
        "SELECT value FROM settings WHERE key=%s",
        (key,),
        fetch=True,
        one=True,
    )
    return int(row["value"]) if row else 0


async def get_user(user_id):
    return await sql(
        "SELECT * FROM users WHERE user_id=%s",
        (user_id,),
        fetch=True,
        one=True,
    )


# ================= KEYBOARDS =================

def main_kb(admin=False):
    rows = [
        [KeyboardButton("🛒 خرید میو پوینت")],
        [KeyboardButton("💰 کیف پول")],
        [KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("👥 دعوت دوستان")],
        [KeyboardButton("🤝 چطور اعتماد کنم")],
        [KeyboardButton("📊 موجودی ما چقدره")],
        [KeyboardButton("📖 راهنما")],
    ]

    if admin:
        rows.append([KeyboardButton("🔐 پنل ادمین")])

    return ReplyKeyboardMarkup(
        rows, resize_keyboard=True
    )


def wallet_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💳 شارژ کیف پول")],
        [KeyboardButton("🔙 بازگشت")],
    ], resize_keyboard=True)


def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 آمار")],
        [KeyboardButton("📢 پیام همگانی")],
        [KeyboardButton("👥 لیست کاربران")],
        [KeyboardButton("💵 قیمت بالای 100")],
        [KeyboardButton("💵 قیمت پایین 100")],
        [KeyboardButton("💰 شارژ همه کاربران")],
        [KeyboardButton("🎁 جایزه همه کاربران")],
        [KeyboardButton("💸 کسر جایزه از همه کاربران")],
        [KeyboardButton("🔙 بازگشت")],
    ], resize_keyboard=True)


def user_manage_kb(user_id):
    actions = [
        ("💰 شارژ کیف پول", "um:add"),
        ("💸 کسر از کیف پول", "um:subtract"),
        ("👥 افزودن رفرال", "um:ref"),
        ("📊 اطلاعات کامل", "um:info"),
        ("📢 ارسال پیام", "um:message"),
        ("📜 سفارش‌ها", "um:orders"),
        ("🎁 افزودن جایزه دعوت", "um:reward"),
        ("📝 یادداشت شخصی", "um:notes"),
        ("📊 تاریخچه تراکنش‌ها", "um:history"),
        ("🗑 حذف کاربر", "um:delete"),
    ]

    buttons = [
        InlineKeyboardButton(
            title,
            callback_data=f"{action}:{user_id}",
        )
        for title, action in actions
    ]

    return InlineKeyboardMarkup([
        [buttons[i], buttons[i + 1]]
        for i in range(0, len(buttons), 2)
    ])


# ================= USER REGISTRATION =================

async def register_user(update, context, referral=None):
    user = update.effective_user

    existing = await get_user(user.id)

    if existing:
        return

    ref_code = secrets.token_hex(5)

    inviter = None

    if referral:
        inviter = await sql(
            "SELECT user_id FROM users WHERE ref_code=%s",
            (referral,),
            fetch=True,
            one=True,
        )

        if inviter and inviter["user_id"] == user.id:
            inviter = None

    inviter_id = inviter["user_id"] if inviter else None

    await sql("""
        INSERT INTO users(
            user_id, ref_code, referred_by,
            full_name, username
        )
        VALUES(%s,%s,%s,%s,%s)
        ON CONFLICT(user_id) DO NOTHING
    """, (
        user.id,
        ref_code,
        inviter_id,
        user.full_name,
        user.username,
    ))

    if inviter_id:
        conn = db_connect()

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE users
                        SET referrals_count=referrals_count+1
                        WHERE user_id=%s
                        RETURNING referrals_count, referral_reward_claimed
                    """, (inviter_id,))

                    result = cur.fetchone()

                    if (
                        result
                        and result["referrals_count"] >= REFERRAL_LIMIT
                        and not result["referral_reward_claimed"]
                    ):
                        cur.execute("""
                            UPDATE users
                            SET total_reward=total_reward+%s,
                                referral_reward_claimed=TRUE
                            WHERE user_id=%s
                        """, (REFERRAL_REWARD, inviter_id))

                        cur.execute("""
                            INSERT INTO transactions(
                                user_id,amount,type,description
                            )
                            VALUES(%s,%s,'referral_reward','Referral reward')
                        """, (inviter_id, REFERRAL_REWARD))
        finally:
            conn.close()


# ================= CHANNEL MEMBERSHIP =================

async def check_membership(bot, user_id):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(
                chat_id=channel,
                user_id=user_id,
            )

            if member.status in ("left", "kicked"):
                return False

        except Exception:
            logger.exception("Channel membership check failed")
            return False

    return True


async def membership_message(update, context):
    buttons = []

    for channel in CHANNELS:
        buttons.append([
            InlineKeyboardButton(
                "عضویت در کانال",
                url=f"https://t.me/{channel.lstrip('@')}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🔄 بررسی عضویت",
            callback_data="check_membership",
        )
    ])

    await update.effective_message.reply_text(
        "برای استفاده از ربات ابتدا در هر دو کانال عضو شو.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ================= START =================

async def start(update, context):
    user = update.effective_user

    if not await check_membership(context.bot, user.id):
        await membership_message(update, context)
        return

    referral = None

    if context.args:
        referral = context.args[0]

    await register_user(update, context, referral)

    await update.message.reply_text(
        f"سلام {user.first_name} 🌸\nبه میو پوینت خوش آمدی.",
        reply_markup=main_kb(is_admin(user.id)),
    )


# ================= PURCHASE =================

async def purchase_menu(update, context):
    await update.message.reply_text(
        "نوع خرید را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "بالای ۱۰۰",
                    callback_data="buy:high",
                ),
                InlineKeyboardButton(
                    "پایین ۱۰۰",
                    callback_data="buy:low",
                ),
            ]
        ]),
    )


async def purchase_amount(update, context):
    query = update.callback_query
    await query.answer()

    kind = query.data.split(":")[1]

    context.user_data["buy_kind"] = kind

    await set_state(
        query.from_user.id,
        "buy_amount",
        kind,
    )

    await query.message.reply_text(
        "تعداد میو پوینت مورد نظر را به عدد وارد کن."
    )


async def process_purchase(update, context, amount):
    user_id = update.effective_user.id

    kind = context.user_data.get("buy_kind")

    if kind not in ("high", "low"):
        state = await get_state(user_id)
        kind = state["data"] if state else None

    if kind not in ("high", "low"):
        await update.message.reply_text("نوع خرید مشخص نیست.")
        return

    if amount <= 0 or amount > 10**12:
        await update.message.reply_text("مقدار واردشده معتبر نیست.")
        return

    if kind == "high" and amount <= 100:
        await update.message.reply_text("برای این دسته باید بیشتر از ۱۰۰ وارد کنی.")
        return

    if kind == "low" and amount > 100:
        await update.message.reply_text("برای این دسته حداکثر ۱۰۰ وارد کن.")
        return

    price = await get_setting(
        "price_high" if kind == "high" else "price_low"
    )

    total = amount * price

    await clear_state(user_id)
    context.user_data.pop("buy_kind", None)

    await update.message.reply_text(
        f"🛒 فاکتور خرید\n\n"
        f"تعداد: {money(amount)}\n"
        f"قیمت هر واحد: {money(price)}\n"
        f"مبلغ کل: {money(total)}\n\n"
        f"شماره کارت:\n`{CARD_NUMBER}`\n"
        f"به نام: {CARD_NAME}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💰 پرداخت با کیف پول",
                    callback_data=f"pay:{amount}:{price}",
                )
            ]
        ]),
    )


async def wallet_purchase(update, context):
    query = update.callback_query
    await query.answer()

    try:
        _, amount, price = query.data.split(":")
        amount = int(amount)
        price = int(price)
    except ValueError:
        await query.message.reply_text("اطلاعات فاکتور نامعتبر است.")
        return

    total = amount * price
    user_id = query.from_user.id

    conn = db_connect()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT wallet FROM users
                    WHERE user_id=%s FOR UPDATE
                """, (user_id,))

                user = cur.fetchone()

                if not user or user["wallet"] < total:
                    await query.message.reply_text(
                        "موجودی کیف پول کافی نیست."
                    )
                    return

                cur.execute("""
                    UPDATE users
                    SET wallet=wallet-%s
                    WHERE user_id=%s
                """, (total, user_id))

                cur.execute("""
                    INSERT INTO transactions(
                        user_id,amount,type,description
                    )
                    VALUES(%s,%s,'purchase','Meow points purchase')
                """, (user_id, total))

        await query.message.reply_text(
            "پرداخت با موفقیت ثبت شد. سفارش شما در انتظار بررسی است."
        )

    finally:
        conn.close()


# ================= WALLET =================

async def show_wallet(update, context):
    user = await get_user(update.effective_user.id)

    if not user:
        await update.message.reply_text("ابتدا /start را بزن.")
        return

    await update.message.reply_text(
        f"💰 موجودی کیف پول: {money(user['wallet'])}",
        reply_markup=wallet_kb(),
    )


async def begin_charge(update, context):
    await set_state(update.effective_user.id, "charge_card")

    await update.message.reply_text(
        "ابتدا عکس کارت بانکی را ارسال کن."
    )


async def handle_photo(update, context):
    user_id = update.effective_user.id
    state = await get_state(user_id)

    if not state:
        return

    photo_id = update.message.photo[-1].file_id

    if state["state"] == "charge_card":
        context.user_data["card_photo"] = photo_id
        await set_state(user_id, "charge_receipt")

        await update.message.reply_text(
            "حالا عکس رسید پرداخت را ارسال کن."
        )
        return

    if state["state"] == "charge_receipt":
        context.user_data["receipt_photo"] = photo_id

        await set_state(user_id, "charge_amount")

        await update.message.reply_text(
            "مبلغ پرداخت‌شده را به عدد وارد کن."
        )


async def create_charge(update, context, amount):
    user_id = update.effective_user.id

    card_photo = context.user_data.get("card_photo")
    receipt_photo = context.user_data.get("receipt_photo")

    if not card_photo or not receipt_photo:
        await update.message.reply_text("تصاویر کامل نیستند.")
        return

    if amount <= 0:
        await update.message.reply_text("مبلغ نامعتبر است.")
        return

    result = await sql("""
        INSERT INTO pending_charges(
            user_id,amount,photo_id,card_photo_id
        )
        VALUES(%s,%s,%s,%s)
        RETURNING id
    """, (user_id, amount, receipt_photo, card_photo), True, True)

    charge_id = result["id"]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ تایید",
                callback_data=f"charge:approve:{charge_id}",
            ),
            InlineKeyboardButton(
                "❌ رد",
                callback_data=f"charge:reject:{charge_id}",
            ),
        ]
    ])

    await context.bot.send_photo(
        ADMIN_ID,
        card_photo,
        caption=f"کارت بانکی کاربر {user_id}",
    )

    await context.bot.send_photo(
        ADMIN_ID,
        receipt_photo,
        caption=(
            f"درخواست شارژ شماره {charge_id}\n"
            f"کاربر: {user_id}\n"
            f"مبلغ: {money(amount)}"
        ),
        reply_markup=keyboard,
    )

    await clear_state(user_id)
    context.user_data.clear()

    await update.message.reply_text(
        "رسید شما برای ادمین ارسال شد و در انتظار بررسی است."
    )


async def charge_decision(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    _, action, charge_id = query.data.split(":")
    charge_id = int(charge_id)

    conn = db_connect()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM pending_charges
                    WHERE id=%s FOR UPDATE
                """, (charge_id,))

                charge = cur.fetchone()

                if not charge or charge["status"] != "pending":
                    await query.message.reply_text(
                        "این درخواست قبلاً بررسی شده است."
                    )
                    return

                if action == "approve":
                    cur.execute("""
                        UPDATE users
                        SET wallet=wallet+%s
                        WHERE user_id=%s
                    """, (charge["amount"], charge["user_id"]))

                    cur.execute("""
                        INSERT INTO transactions(
                            user_id,amount,type,description
                        )
                        VALUES(%s,%s,'deposit','Approved deposit')
                    """, (charge["user_id"], charge["amount"]))

                    status = "approved"

                else:
                    status = "rejected"

                cur.execute("""
                    UPDATE pending_charges
                    SET status=%s
                    WHERE id=%s
                """, (status, charge_id))

        await context.bot.send_message(
            charge["user_id"],
            "درخواست شارژ شما تایید شد."
            if action == "approve"
            else "درخواست شارژ شما رد شد.",
        )

        await query.edit_message_reply_markup(
            reply_markup=None
        )

    finally:
        conn.close()


# ================= REFERRAL =================

async def referral_menu(update, context):
    user_id = update.effective_user.id
    user = await get_user(user_id)

    if not user:
        return

    bot = await context.bot.get_me()

    link = f"https://t.me/{bot.username}?start={user['ref_code']}"

    await update.message.reply_text(
        f"👥 لینک دعوت شما:\n{link}\n\n"
        f"تعداد دعوت‌ها: {user['referrals_count']}\n"
        f"مجموع جوایز: {money(user['total_reward'])}\n\n"
        f"🎁 شرایط دریافت جایزه:\n"
        f"دعوت حداقل {REFERRAL_LIMIT} نفر\n"
        f"جایزه: {money(REFERRAL_REWARD)} میو"
    )


# ================= SUPPORT =================

async def support_start(update, context):
    await set_state(update.effective_user.id, "support")

    await update.message.reply_text(
        "پیام خود را برای پشتیبانی ارسال کن."
    )


async def support_message(update, context):
    user = update.effective_user

    await context.bot.send_message(
        ADMIN_ID,
        f"📞 پیام پشتیبانی\n"
        f"کاربر: {user.id}\n"
        f"نام: {user.full_name}\n\n"
        f"{update.message.text}",
    )

    await update.message.reply_text(
        "پیام شما برای پشتیبانی ارسال شد."
    )

    await clear_state(user.id)


# ================= ADMIN STATS =================

async def admin_stats(update, context):
    if not is_admin(update.effective_user.id):
        return

    result = await sql("""
        SELECT
            (SELECT COUNT(*) FROM users) AS users,
            (SELECT COALESCE(SUM(wallet),0) FROM users) AS wallet,
            (SELECT COUNT(*) FROM transactions
             WHERE type='purchase') AS orders,
            (SELECT COUNT(*) FROM pending_charges
             WHERE status='pending') AS pending
    """, fetch=True, one=True)

    await update.message.reply_text(
        "📊 آمار ربات\n\n"
        f"👥 کاربران: {result['users']}\n"
        f"💰 مجموع کیف پول: {money(result['wallet'])}\n"
        f"🛒 سفارش‌ها: {result['orders']}\n"
        f"🧾 رسیدهای در انتظار: {result['pending']}",
        reply_markup=admin_kb(),
    )


# ================= ADMIN USER LIST =================

async def user_list(update, context, page=0, search=None):
    if not is_admin(update.effective_user.id):
        return

    page = max(0, page)
    offset = page * 10

    if search:
        users = await sql("""
            SELECT user_id,full_name,username,wallet
            FROM users
            WHERE CAST(user_id AS TEXT) ILIKE %s
               OR full_name ILIKE %s
               OR username ILIKE %s
            ORDER BY joined_at DESC
            LIMIT 10
        """, (f"%{search}%", f"%{search}%", f"%{search}%"), True)
    else:
        users = await sql("""
            SELECT user_id,full_name,username,wallet
            FROM users
            ORDER BY joined_at DESC
            LIMIT 10 OFFSET %s
        """, (offset,), True)

    buttons = []

    for user in users:
        name = user["full_name"] or "بدون نام"

        buttons.append([
            InlineKeyboardButton(
                f"{name} | {user['user_id']}",
                callback_data=f"select_user:{user['user_id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ قبلی",
            callback_data=f"users_page:{max(0,page-1)}",
        ),
        InlineKeyboardButton(
            "بعدی ➡️",
            callback_data=f"users_page:{page+1}",
        ),
    ])

    buttons.append([
        InlineKeyboardButton(
            "🔎 جستجو",
            callback_data="users_search",
        )
    ])

    markup = InlineKeyboardMarkup(buttons)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"👥 کاربران | صفحه {page+1}",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            f"👥 کاربران | صفحه {page+1}",
            reply_markup=markup,
        )


# ================= USER MANAGEMENT =================

async def user_info(query, user_id):
    user = await get_user(user_id)

    if not user:
        await query.message.reply_text("کاربر پیدا نشد.")
        return

    await query.message.reply_text(
        f"📊 اطلاعات کاربر\n\n"
        f"آیدی: {user['user_id']}\n"
        f"نام: {user['full_name']}\n"
        f"یوزرنیم: @{user['username'] or '-'}\n"
        f"کیف پول: {money(user['wallet'])}\n"
        f"دعوت‌ها: {user['referrals_count']}\n"
        f"جایزه: {money(user['total_reward'])}\n"
        f"تاریخ عضویت: {user['joined_at']}\n"
        f"یادداشت: {user['notes'] or '-'}",
        reply_markup=user_manage_kb(user_id),
    )


async def user_transactions(query, user_id, kind="all"):
    if kind == "orders":
        rows = await sql("""
            SELECT * FROM transactions
            WHERE user_id=%s AND type='purchase'
            ORDER BY created_at DESC LIMIT 20
        """, (user_id,), True)
    else:
        rows = await sql("""
            SELECT * FROM transactions
            WHERE user_id=%s
            ORDER BY created_at DESC LIMIT 30
        """, (user_id,), True)

    if not rows:
        await query.message.reply_text("تراکنشی ثبت نشده است.")
        return

    text = "📜 تاریخچه تراکنش‌ها\n\n"

    for row in rows:
        text += (
            f"#{row['id']} | {row['type']}\n"
            f"مبلغ: {money(row['amount'])}\n"
            f"{row['description'] or ''}\n"
            f"تاریخ: {row['created_at']}\n\n"
        )

    await query.message.reply_text(text[:4000])


async def admin_user_action(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    parts = query.data.split(":")
    action = parts[1]
    user_id = int(parts[2])

    if action == "info":
        await user_info(query, user_id)
        return

    if action == "orders":
        await user_transactions(query, user_id, "orders")
        return

    if action == "history":
        await user_transactions(query, user_id)
        return

    if action == "delete":
        await set_state(query.from_user.id, "confirm_delete", user_id)

        await query.message.reply_text(
            "آیا از حذف این کاربر مطمئنی؟",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "بله، حذف شود",
                        callback_data=f"confirm_delete:{user_id}",
                    ),
                    InlineKeyboardButton(
                        "لغو",
                        callback_data="cancel_admin",
                    ),
                ]
            ]),
        )
        return

    state_map = {
        "add": "admin_add",
        "subtract": "admin_subtract",
        "ref": "admin_ref",
        "message": "admin_message",
        "reward": "admin_reward",
        "notes": "admin_notes",
    }

    if action in state_map:
        await set_state(
            query.from_user.id,
            state_map[action],
            user_id,
        )

        prompts = {
            "add": "مبلغ شارژ را وارد کن.",
            "subtract": "مبلغ کسر را وارد کن.",
            "ref": "تعداد رفرال را وارد کن.",
            "message": "متن پیام را وارد کن.",
            "reward": "مبلغ جایزه را وارد کن.",
            "notes": "یادداشت را وارد کن.",
        }

        await query.message.reply_text(prompts[action])


async def confirm_delete(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    user_id = int(query.data.split(":")[1])

    await sql(
        "DELETE FROM users WHERE user_id=%s",
        (user_id,),
    )

    await sql(
        "DELETE FROM transactions WHERE user_id=%s",
        (user_id,),
    )

    await sql(
        "DELETE FROM pending_charges WHERE user_id=%s",
        (user_id,),
    )

    await query.edit_message_text("کاربر حذف شد.")


# ================= ADMIN BULK =================

async def broadcast(update, context, text):
    users = await sql(
        "SELECT user_id FROM users",
        fetch=True,
    )

    sent = 0
    failed = 0

    for user in users:
        try:
            await context.bot.send_message(
                user["user_id"], text
            )
            sent += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.04)

    await update.message.reply_text(
        f"ارسال تمام شد.\nموفق: {sent}\nناموفق: {failed}"
    )


async def bulk_wallet(amount, reward=False):
    if reward:
        await sql("""
            UPDATE users
            SET total_reward=total_reward+%s
        """, (amount,))
        return

    conn = db_connect()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users SET wallet=wallet+%s
                    RETURNING user_id
                """, (amount,))

                ids = [r["user_id"] for r in cur.fetchall()]

                for uid in ids:
                    cur.execute("""
                        INSERT INTO transactions(
                            user_id,amount,type,description
                        )
                        VALUES(%s,%s,'admin_credit','Bulk wallet credit')
                    """, (uid, amount))
    finally:
        conn.close()


async def bulk_reward_subtract(amount):
    await sql("""
        UPDATE users
        SET total_reward=GREATEST(0,total_reward-%s)
    """, (amount,))


# ================= TEXT ROUTER =================

async def text_router(update, context):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    state = await get_state(user_id)

    # ---------- ADMIN STATE ----------

    if is_admin(user_id) and state:

        if state["state"] == "confirm_delete":
            await update.message.reply_text(
                "از دکمه تأیید یا لغو استفاده کن."
            )
            return

        if state["state"] == "admin_search":
            await clear_state(user_id)
            await user_list(update, context, search=text)
            return

        if state["state"] in (
            "admin_add",
            "admin_subtract",
            "admin_ref",
            "admin_reward",
        ):
            try:
                amount = int(text)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(
                    "لطفاً یک عدد مثبت وارد کن."
                )
                return

            target = int(state["data"])

            if state["state"] == "admin_add":
                await sql("""
                    UPDATE users SET wallet=wallet+%s
                    WHERE user_id=%s
                """, (amount, target))

                await log_transaction(
                    target, amount, "admin_credit", "Admin wallet credit"
                )

            elif state["state"] == "admin_subtract":
                conn = db_connect()
                try:
                    with conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE users
                                SET wallet=GREATEST(0,wallet-%s)
                                WHERE user_id=%s
                                RETURNING wallet
                            """, (amount, target))
                            result = cur.fetchone()

                            if result:
                                cur.execute("""
                                    INSERT INTO transactions(
                                        user_id,amount,type,description
                                    )
                                    VALUES(%s,%s,'admin_debit','Admin wallet debit')
                                """, (target, amount))
                finally:
                    conn.close()

            elif state["state"] == "admin_ref":
                await sql("""
                    UPDATE users
                    SET referrals_count=referrals_count+%s
                    WHERE user_id=%s
                """, (amount, target))

            elif state["state"] == "admin_reward":
                await sql("""
                    UPDATE users
                    SET total_reward=total_reward+%s
                    WHERE user_id=%s
                """, (amount, target))

            await clear_state(user_id)
            await update.message.reply_text("عملیات انجام شد.")
            return

        if state["state"] == "admin_message":
            target = int(state["data"])

            try:
                await context.bot.send_message(target, text)
                await update.message.reply_text("پیام ارسال شد.")
            except Exception:
                await update.message.reply_text("ارسال پیام ناموفق بود.")

            await clear_state(user_id)
            return

        if state["state"] == "admin_notes":
            target = int(state["data"])

            await sql("""
                UPDATE users SET notes=%s WHERE user_id=%s
            """, (text, target))

            await clear_state(user_id)
            await update.message.reply_text("یادداشت ذخیره شد.")
            return

        if state["state"] == "admin_broadcast":
            await clear_state(user_id)
            await broadcast(update, context, text)
            return

        if state["state"] in (
            "admin_bulk_wallet",
            "admin_bulk_reward",
            "admin_bulk_subtract",
        ):
            try:
                amount = int(text)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("مبلغ نامعتبر است.")
                return

            if state["state"] == "admin_bulk_wallet":
                await bulk_wallet(amount)

            elif state["state"] == "admin_bulk_reward":
                await bulk_wallet(amount, True)

            else:
                await bulk_reward_subtract(amount)

            await clear_state(user_id)
            await update.message.reply_text("عملیات گروهی انجام شد.")
            return

        if state["state"] in ("price_high", "price_low"):
            try:
                amount = int(text)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("قیمت نامعتبر است.")
                return

            await sql("""
                INSERT INTO settings(key,value)
                VALUES(%s,%s)
                ON CONFLICT(key)
                DO UPDATE SET value=EXCLUDED.value
            """, (state["state"], str(amount)))

            await clear_state(user_id)
            await update.message.reply_text("قیمت جدید ذخیره شد.")
            return

    # ---------- USER STATES ----------

    if state:

        if state["state"] == "buy_amount":
            try:
                amount = int(text)
            except ValueError:
                await update.message.reply_text("فقط عدد وارد کن.")
                return

            await process_purchase(update, context, amount)
            return

        if state["state"] == "charge_amount":
            try:
                amount = int(text)
            except ValueError:
                await update.message.reply_text("مبلغ نامعتبر است.")
                return

            await create_charge(update, context, amount)
            return

        if state["state"] == "support":
            await support_message(update, context)
            return

    # ---------- MAIN MENU ----------

    if text == "🔙 بازگشت":
        await clear_state(user_id)

        await update.message.reply_text(
            "منوی اصلی",
            reply_markup=main_kb(is_admin(user_id)),
        )
        return

    if text == "🛒 خرید میو پوینت":
        await purchase_menu(update, context)
        return

    if text == "💰 کیف پول":
        await show_wallet(update, context)
        return

    if text == "💳 شارژ کیف پول":
        await begin_charge(update, context)
        return

    if text == "📞 پشتیبانی":
        await support_start(update, context)
        return

    if text == "👥 دعوت دوستان":
        await referral_menu(update, context)
        return

    if text == "🤝 چطور اعتماد کنم":
        await update.message.reply_text(
            "برای اطمینان از خرید، می‌توانی قبل از پرداخت "
            "از طریق پشتیبانی با ما در ارتباط باشی."
        )
        return

    if text == "📊 موجودی ما چقدره":
        await update.message.reply_text(
            "برای مشاهده موجودی مجموعه با پشتیبانی تماس بگیر."
        )
        return

    if text == "📖 راهنما":
        await update.message.reply_text(
            "راهنمای ربات:\n"
            "۱. خرید میو پوینت\n"
            "۲. شارژ کیف پول\n"
            "۳. دعوت دوستان\n"
            "۴. ارتباط با پشتیبانی"
        )
        return

    # ---------- ADMIN MENU ----------

    if is_admin(user_id):

        if text == "🔐 پنل ادمین":
            await update.message.reply_text(
                "پنل مدیریت",
                reply_markup=admin_kb(),
            )
            return

        if text == "📊 آمار":
            await admin_stats(update, context)
            return

        if text == "👥 لیست کاربران":
            await user_list(update, context)
            return

        if text == "📢 پیام همگانی":
            await set_state(user_id, "admin_broadcast")
            await update.message.reply_text("متن پیام همگانی را بفرست.")
            return

        if text == "💰 شارژ همه کاربران":
            await set_state(user_id, "admin_bulk_wallet")
            await update.message.reply_text("مبلغ شارژ را وارد کن.")
            return

        if text == "🎁 جایزه همه کاربران":
            await set_state(user_id, "admin_bulk_reward")
            await update.message.reply_text("مبلغ جایزه را وارد کن.")
            return

        if text == "💸 کسر جایزه از همه کاربران":
            await set_state(user_id, "admin_bulk_subtract")
            await update.message.reply_text("مبلغ کسر را وارد کن.")
            return

        if text == "💵 قیمت بالای 100":
            await set_state(user_id, "price_high")
            await update.message.reply_text("قیمت جدید را وارد کن.")
            return

        if text == "💵 قیمت پایین 100":
            await set_state(user_id, "price_low")
            await update.message.reply_text("قیمت جدید را وارد کن.")
            return

    await update.message.reply_text(
        "گزینه نامعتبر است. از منوی ربات استفاده کن."
    )


# ================= CALLBACK ROUTER =================

async def callback_router(update, context):
    query = update.callback_query
    data = query.data

    if data == "check_membership":
        await query.answer()

        if await check_membership(context.bot, query.from_user.id):
            await register_user(update, context)

            await query.message.reply_text(
                "عضویت تأیید شد.",
                reply_markup=main_kb(is_admin(query.from_user.id)),
            )
        else:
            await query.answer(
                "هنوز در هر دو کانال عضو نشده‌ای.",
                show_alert=True,
            )
        return

    if data.startswith("buy:"):
        await purchase_amount(update, context)
        return

    if data.startswith("pay:"):
        await wallet_purchase(update, context)
        return

    if data.startswith("charge:"):
        await charge_decision(update, context)
        return

    if data.startswith("users_page:"):
        await query.answer()

        if not is_admin(query.from_user.id):
            return

        page = int(data.split(":")[1])
        await user_list(update, context, page)
        return

    if data == "users_search":
        await query.answer()

        if not is_admin(query.from_user.id):
            return

        await set_state(query.from_user.id, "admin_search")
        await query.message.reply_text("نام، یوزرنیم یا آیدی را وارد کن.")
        return

    if data.startswith("select_user:"):
        await query.answer()

        if not is_admin(query.from_user.id):
            return

        user_id = int(data.split(":")[1])

        await query.message.reply_text(
            f"مدیریت کاربر {user_id}",
            reply_markup=user_manage_kb(user_id),
        )
        return

    if data.startswith("um:"):
        await admin_user_action(update, context)
        return

    if data.startswith("confirm_delete:"):
        await confirm_delete(update, context)
        return

    if data == "cancel_admin":
        await query.answer("لغو شد.")
        await clear_state(query.from_user.id)
        await query.edit_message_text("عملیات لغو شد.")
        return


# ================= AIOHTTP SERVER =================

async def health(request):
    return web.Response(text="Meow Bot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    logger.info("Web server started on port %s", port)


# ================= ERROR HANDLER =================

async def error_handler(update, context):
    logger.error(
        "Unhandled exception",
        exc_info=context.error,
    )

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "خطایی رخ داد. لطفاً دوباره تلاش کن."
            )
        except Exception:
            pass


# ================= MAIN =================

async def post_init(application):
    await start_web_server()


def main():
    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))

    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    application.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    application.add_error_handler(error_handler)

    # Python 3.14 event loop setup
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
