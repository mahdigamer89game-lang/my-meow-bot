import asyncio
import sqlite3
import logging
import os
from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

TOKEN = os.environ.get("TOKEN")
ADMIN_ID = 1713081033
SUPPORT_USERNAME = "@Goooorba1234"
CARD_NUMBER = "5047061673513814"
CARD_NAME = "صادقی"

logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect("bot.db", check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    wallet INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS pending_charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    photo_id TEXT,
    status TEXT DEFAULT 'pending'
)""")
c.execute("""CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)""")
conn.commit()

def get_setting(key, default):
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    r = c.fetchone()
    if r is None:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, str(default)))
        conn.commit()
        return default
    return int(r[0])

def set_setting(key, value):
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()

def get_price_high():
    return get_setting("price_high", 2100)

def get_price_low():
    return get_setting("price_low", 2500)

def get_threshold():
    return get_setting("threshold", 100)

def get_bonus_threshold():
    return get_setting("bonus_threshold", 150)

def get_bonus_amount():
    return get_setting("bonus_amount", 3)

def get_wallet(uid):
    c.execute("SELECT wallet FROM users WHERE user_id=?", (uid,))
    r = c.fetchone()
    if r is None:
        c.execute("INSERT INTO users (user_id, wallet) VALUES (?, 0)", (uid,))
        conn.commit()
        return 0
    return r[0]

def add_wallet(uid, delta):
    new = get_wallet(uid) + delta
    c.execute("INSERT OR REPLACE INTO users (user_id, wallet) VALUES (?, ?)", (uid, new))
    conn.commit()
    return new

def get_all_users():
    c.execute("SELECT user_id FROM users")
    return [r[0] for r in c.fetchall()]

def fa_to_en(text):
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

(MENU, BUY_CAT, BUY_AMOUNT, BUY_CARD, WALLET_MENU, WALLET_AMOUNT, WALLET_PHOTO,
 ADMIN_PANEL, ADMIN_BROADCAST, ADMIN_SET_PRICE_HIGH, ADMIN_SET_PRICE_LOW,
 SUPPORT_MESSAGE) = range(12)

def is_admin(uid):
    return uid == ADMIN_ID

def main_kb(uid=None):
    rows = [
        [KeyboardButton("🛒 خرید میو پوینت")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("🤝 چطور اعتماد کنم"), KeyboardButton("📖 راهنما")],
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
    get_wallet(uid)
    context.user_data.clear()
    await update.message.reply_text(
        "به ربات خوش آمدید! 👋\nلطفا یک گزینه را انتخاب کنید:",
        reply_markup=main_kb(uid)
    )
    return MENU

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    if text == "🛒 خرید میو پوینت":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("بالای 100 میلیون", callback_data="cat_high")],
            [InlineKeyboardButton("پایین 100 میلیون", callback_data="cat_low")]
        ])
        await update.message.reply_text("لطفا یک دسته را انتخاب کنید:", reply_markup=kb)
        return BUY_CAT
    elif text == "💰 کیف پول":
        bal = get_wallet(uid)
        await update.message.reply_text(
            f"💰 موجودی کیف پول شما: {bal:,} تومان",
            reply_markup=wallet_kb()
        )
        return WALLET_MENU
    elif text == "📞 پشتیبانی":
        await update.message.reply_text(
            "شما در حال تیکت به پشتیبانی هستید\nپیام خود را ارسال کنید:",
            reply_markup=cancel_kb()
        )
        return SUPPORT_MESSAGE
    elif text == "🤝 چطور اعتماد کنم":
        await update.message.reply_text(
            "ما یک چنل داریم که در آن رضایت‌ها گذاشته می‌شوند\n"
            "https://t.me/Meow_Point_Free\n\n"
            "برای دیدن رضایت‌ها در چنل سرچ کنید #رضایت تا رضایت‌ها نمایش داده شوند\n"
            "و اگر سوالی داشتید در پی وی بگید @Goooorba1234"
        )
        return MENU
    elif text == "📖 راهنما":
        await update.message.reply_text(
            "راهنمای خرید میوپوینت\n\n"
            "اول باید کیف پول را شارژ کنید\n"
            "وارد بخش کیف پول شوید و آن را شارژ کنید\n"
            "بعد از شارژ کردن به بخش خرید میو پوینت بروید و از آنجا می‌توانید مقداری که می‌خواین رو وارد کنید\n\n"
            "اگر سوالی داشتید پی وی بگید @Goooorba1234"
        )
        return MENU
    elif text == "🔐 پنل ادمین" and is_admin(uid):
        await update.message.reply_text(
            "🔐 پنل ادمین\n\nلطفا یک گزینه را انتخاب کنید:",
            reply_markup=admin_kb()
        )
        return ADMIN_PANEL

async def admin_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    if not is_admin(uid):
        return MENU
    if text == "📢 پیام همگانی":
        await update.message.reply_text("متن پیام همگانی را وارد کنید:", reply_markup=cancel_kb())
        return ADMIN_BROADCAST
    elif text == "💵 قیمت بالای 100":
        current = get_price_high()
        await update.message.reply_text(
            f"قیمت فعلی بالای 100 میلیون: {current:,} تومان\n\nقیمت جدید را وارد کنید (فقط عدد):",
            reply_markup=cancel_kb()
        )
        return ADMIN_SET_PRICE_HIGH
    elif text == "💵 قیمت پایین 100":
        current = get_price_low()
        await update.message.reply_text(
            f"قیمت فعلی پایین 100 میلیون: {current:,} تومان\n\nقیمت جدید را وارد کنید (فقط عدد):",
            reply_markup=cancel_kb()
        )
        return ADMIN_SET_PRICE_LOW
    elif text == "🔙 بازگشت":
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
        return MENU

async def admin_broadcast(update, context):
    if not is_admin(update.effective_user.id):
        return MENU
    if update.message.text == "🔙 بازگشت":
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return ADMIN_PANEL
    text = update.message.text
    users = get_all_users()
    sent = 0
    failed = 0
    await update.message.reply_text(f"در حال ارسال به {len(users)} کاربر...")
    for u in users:
        try:
            await context.bot.send_message(u, f"📢 اطلاعیه:\n\n{text}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"✅ ارسال شد به {sent} کاربر\n❌ ناموفق: {failed}",
        reply_markup=admin_kb()
    )
    return ADMIN_PANEL

async def admin_set_price_high(update, context):
    if not is_admin(update.effective_user.id):
        return MENU
    if update.message.text == "🔙 بازگشت":
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return ADMIN_PANEL
    try:
        val = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if val <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return ADMIN_SET_PRICE_HIGH
    set_setting("price_high", val)
    await update.message.reply_text(f"✅ قیمت بالای 100 میلیون به {val:,} تومان تغییر کرد.", reply_markup=admin_kb())
    return ADMIN_PANEL

async def admin_set_price_low(update, context):
    if not is_admin(update.effective_user.id):
        return MENU
    if update.message.text == "🔙 بازگشت":
        await update.message.reply_text("لغو شد.", reply_markup=admin_kb())
        return ADMIN_PANEL
    try:
        val = int(fa_to_en(update.message.text.strip()).replace(",", ""))
        if val <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return ADMIN_SET_PRICE_LOW
    set_setting("price_low", val)
    await update.message.reply_text(f"✅ قیمت پایین 100 میلیون به {val:,} تومان تغییر کرد.", reply_markup=admin_kb())
    return ADMIN_PANEL

async def support_message(update, context):
    if update.message.text == "🔙 بازگشت":
        await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(update.effective_user.id))
        return MENU
    uid = update.effective_user.id
    user = update.effective_user
    text = update.message.text
    admin_text = (
        f"💬 تیکت جدید\n\n"
        f"🆔 آیدی کاربر: {uid}\n"
        f"👤 نام: {user.full_name}\n"
        f"🔗 یوزرنیم: @{user.username if user.username else 'ندارد'}\n\n"
        f"📝 پیام:\n{text}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ پاسخ", callback_data=f"reply_{uid}")]])
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)
    await update.message.reply_text("✅ پیام شما ارسال شد. به زودی پاسخ می‌گیرید.", reply_markup=main_kb(uid))
    return MENU

async def buy_cat_selected(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cat_high":
        context.user_data["buy_cat"] = "high"
        await query.edit_message_text("شما «بالای 100 میلیون» را انتخاب کردید.\nلطفا مقدار (به میلیون) را وارد کنید:")
    else:
        context.user_data["buy_cat"] = "low"
        await query.edit_message_text("شما «پایین 100 میلیون» را انتخاب کردید.\nلطفا مقدار (به میلیون) را وارد کنید:")
    return BUY_AMOUNT

async def buy_amount_entered(update, context):
    text = fa_to_en(update.message.text.strip())
    try:
        amount = float(text)
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return BUY_AMOUNT
    cat = context.user_data.get("buy_cat")
    threshold = get_threshold()
    if cat == "high" and amount < threshold:
        await update.message.reply_text(f"لطفا مقدار بالای {threshold} میلیون وارد کنید.")
        return BUY_AMOUNT
    if cat == "low" and amount >= threshold:
        await update.message.reply_text(f"لطفا مقدار پایین {threshold} میلیون وارد کنید.")
        return BUY_AMOUNT
    price = get_price_high() if cat == "high" else get_price_low()
    total = int(amount * price)
    context.user_data["buy_amount"] = amount
    context.user_data["buy_total"] = total
    context.user_data["awaiting_pay"] = False
    await update.message.reply_text(
        f"✅ مقدار: {amount:g} میلیون\n"
        f"💵 قیمت هر میلیون: {price:,} تومان\n"
        f"💰 مبلغ کل: {total:,} تومان\n\n"
        f"لطفا شماره کارت میویی خود را وارد کنید:"
    )
    return BUY_CARD

async def buy_card_entered(update, context):
    if context.user_data.get("awaiting_pay"):
        await update.message.reply_text("لطفا روی دکمه «💳 پرداخت با کیف پول» بزنید.")
        return BUY_CARD
    card = update.message.text.strip()
    context.user_data["buy_card"] = card
    context.user_data["awaiting_pay"] = True
    total = context.user_data["buy_total"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💳 پرداخت با کیف پول", callback_data="pay_wallet")]])
    await update.message.reply_text(
        f"شماره کارت شما ثبت شد: {card}\nمبلغ قابل پرداخت: {total:,} تومان\n\nبرای پرداخت روی دکمه زیر بزنید:",
        reply_markup=kb
    )
    return BUY_CARD

async def pay_wallet(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    total = context.user_data.get("buy_total", 0)
    bal = get_wallet(uid)
    if bal < total:
        await query.edit_message_text(
            f"❌ موجودی کافی نمیباشد.\nموجودی شما: {bal:,} تومان\nمبلغ لازم: {total:,} تومان\n\nلطفا ابتدا از بخش کیف پول، آن را شارژ کنید."
        )
        return MENU
    new_bal = add_wallet(uid, -total)
    amount = context.user_data.get("buy_amount", 0)
    card = context.user_data.get("buy_card", "-")
    bonus_threshold = get_bonus_threshold()
    bonus_amount = get_bonus_amount()
    bonus_text = ""
    if amount >= bonus_threshold:
        bonus_text = f"\n🎁 پاداش: +{bonus_amount} میلیون (به رسید اضافه شد)"
    admin_text = (
        f"🛒 سفارش جدید میو پوینت\n\n"
        f"🆔 آیدی کاربر: {uid}\n"
        f"👤 نام: {query.from_user.full_name}\n"
        f"💳 شماره کارت میویی: {card}\n"
        f"📊 مقدار: {amount:g}{bonus_text}\n"
        f"💰 مبلغ کل: {total:,} تومان\n"
        f"💼 موجودی باقی‌مانده کاربر: {new_bal:,} تومان"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ واریز شد", callback_data=f"deliver_{uid}")]])
    try:
        await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=kb)
    except Exception as e:
        logging.error(e)
    await query.edit_message_text(
        f"✅ پرداخت انجام شد.\nمبلغ {total:,} تومان از کیف پول شما کسر شد.\nموجودی جدید: {new_bal:,} تومان\n\nسفارش شما ثبت شد و به زودی میو برای شما واریز می‌شود."
    )
    await context.bot.send_message(uid, "به منوی اصلی بازگشتید 👇", reply_markup=main_kb(uid))
    context.user_data.clear()
    return MENU
async def deliver_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)
        return
    target_uid = int(query.data.replace("deliver_", ""))
    try:
        await context.bot.send_message(
            target_uid,
            "✅ میو پوینت شما واریز شد!\n\nاز خرید شما متشکریم. 🌸"
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ پیام واریز به کاربر {target_uid} ارسال شد.")
    except Exception as e:
        logging.error(e)
        await query.message.reply_text(f"❌ خطا در ارسال پیام: {e}")

async def reply_callback(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    target_uid = int(query.data.replace("reply_", ""))
    context.user_data["reply_to"] = target_uid
    await query.message.reply_text(
        f"✍️ پاسخ خود را برای کاربر {target_uid} بنویسید:\n(برای لغو /start را بزنید)"
    )

async def send_reply(update, context):
    target_uid = context.user_data.get("reply_to")
    if not target_uid:
        return
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text
    try:
        await context.bot.send_message(
            target_uid,
            f"📩 پاسخ پشتیبانی:\n\n{text}",
            reply_markup=main_kb(target_uid)
        )
        await update.message.reply_text(
            f"✅ پاسخ به کاربر {target_uid} ارسال شد.",
            reply_markup=main_kb(update.effective_user.id)
        )
    except Exception as e:
        logging.error(e)
        await update.message.reply_text(f"❌ خطا: {e}")
    context.user_data.pop("reply_to", None)

async def wallet_charge_start(update, context):
    await update.message.reply_text(
        f"شما در حال کارت به کارت هستید\nشماره کارت: {CARD_NUMBER}\nبنام {CARD_NAME}\n\nلطفا مبلغ پرداخت را وارد کنید:"
    )
    return WALLET_AMOUNT

async def wallet_amount_entered(update, context):
    text = fa_to_en(update.message.text.strip()).replace(",", "").replace("،", "")
    try:
        amount = int(text)
        if amount <= 0: raise ValueError
    except:
        await update.message.reply_text("لطفا یک عدد معتبر وارد کنید.")
        return WALLET_AMOUNT
    context.user_data["charge_amount"] = amount
    await update.message.reply_text("لطفا عکس رسید پرداخت را ارسال کنید:")
    return WALLET_PHOTO

async def wallet_photo_received(update, context):
    uid = update.effective_user.id
    amount = context.user_data.get("charge_amount", 0)
    photo_id = update.message.photo[-1].file_id
    c.execute("INSERT INTO pending_charges (user_id, amount, photo_id) VALUES (?, ?, ?)", (uid, amount, photo_id))
    conn.commit()
    charge_id = c.lastrowid
    await update.message.reply_text("لطفا منتظر تایید باشید...", reply_markup=main_kb(uid))
    admin_text = (
        f"💳 درخواست شارژ کیف پول\n\n"
        f"🆔 آیدی کاربر: {uid}\n"
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
    context.user_data.clear()
    return MENU

async def charge_callback(update, context):
    query = update.callback_query
    data = query.data
    if data.startswith("charge_ok_"):
        charge_id = int(data.replace("charge_ok_", ""))
        c.execute("SELECT user_id, amount, status FROM pending_charges WHERE id=?", (charge_id,))
        row = c.fetchone()
        if not row:
            await query.answer("درخواست یافت نشد.", show_alert=True)
            return
        uid, amount, status = row
        if status != "pending":
            await query.answer("قبلا پردازش شده.", show_alert=True)
            return
        add_wallet(uid, amount)
        c.execute("UPDATE pending_charges SET status='approved' WHERE id=?", (charge_id,))
        conn.commit()
        await query.edit_message_caption(f"✅ تایید شد — {amount:,} تومان به کاربر {uid} اضافه شد.")
        try:
            await context.bot.send_message(uid, f"✅ رسید شما تایید شد و کیف پول شما به مبلغ {amount:,} تومان شارژ شد.")
        except Exception as e:
            logging.error(e)
    elif data.startswith("charge_no_"):
        charge_id = int(data.replace("charge_no_", ""))
        c.execute("SELECT user_id, amount, status FROM pending_charges WHERE id=?", (charge_id,))
        row = c.fetchone()
        if not row:
            await query.answer("درخواست یافت نشد.", show_alert=True)
            return
        uid, amount, status = row
        if status != "pending":
            await query.answer("قبلا پردازش شده.", show_alert=True)
            return
        c.execute("UPDATE pending_charges SET status='rejected' WHERE id=?", (charge_id,))
        conn.commit()
        await query.edit_message_caption(f"❌ رد شد — درخواست شارژ کاربر {uid} رد شد.")
        try:
            await context.bot.send_message(uid, "❌ متاسفانه رسید شما تایید نشد. برای پیگیری با پشتیبانی تماس بگیرید.")
        except Exception as e:
            logging.error(e)
    await query.answer()

async def back_to_menu(update, context):
    await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb(update.effective_user.id))
    return MENU

app = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MENU: [
            MessageHandler(filters.Regex("^🛒 خرید میو پوینت$"), menu_router),
            MessageHandler(filters.Regex("^💰 کیف پول$"), menu_router),
            MessageHandler(filters.Regex("^📞 پشتیبانی$"), menu_router),
            MessageHandler(filters.Regex("^🤝 چطور اعتماد کنم$"), menu_router),
            MessageHandler(filters.Regex("^📖 راهنما$"), menu_router),
            MessageHandler(filters.Regex("^🔐 پنل ادمین$"), menu_router),
        ],
        BUY_CAT: [
            CallbackQueryHandler(buy_cat_selected, pattern="^cat_"),
        ],
        BUY_AMOUNT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_amount_entered),
        ],
        BUY_CARD: [
            CallbackQueryHandler(pay_wallet, pattern="^pay_wallet$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, buy_card_entered),
        ],
        WALLET_MENU: [
            MessageHandler(filters.Regex("^💳 شارژ کیف پول$"), wallet_charge_start),
            MessageHandler(filters.Regex("^🔙 بازگشت$"), back_to_menu),
        ],
        WALLET_AMOUNT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_amount_entered),
        ],
        WALLET_PHOTO: [
            MessageHandler(filters.PHOTO, wallet_photo_received),
        ],
        ADMIN_PANEL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_router),
        ],
        ADMIN_BROADCAST: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast),
        ],
        ADMIN_SET_PRICE_HIGH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_price_high),
        ],
        ADMIN_SET_PRICE_LOW: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_price_low),
        ],
        SUPPORT_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_message),
        ],
    },
    fallbacks=[
        CommandHandler("start", start),
        CallbackQueryHandler(charge_callback, pattern="^charge_"),
        CallbackQueryHandler(deliver_callback, pattern="^deliver_"),
        CallbackQueryHandler(reply_callback, pattern="^reply_"),
    ],
    allow_reentry=True,
)

app.add_handler(conv, group=0)
app.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
    send_reply
), group=2)

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
