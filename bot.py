import asyncio
import sqlite3
import logging
import os
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ============ تنظیمات ============
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = 1713081033
SUPPORT_USERNAME = "@Goooorba1234"
CARD_NUMBER = "5047061673513814"
CARD_NAME = "صادقی"
PRICE_LOW = 2500     # پایین 100 میلیون → هر 1 میلیون
PRICE_HIGH = 2100    # بالای 100 میلیون → هر 1 میلیون
THRESHOLD = 100      # میلیون

logging.basicConfig(level=logging.INFO)

# ============ دیتابیس ============
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
conn.commit()

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

def fa_to_en(text):
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))

# ============ State ها ============
MENU, BUY_CAT, BUY_AMOUNT, BUY_CARD, WALLET_MENU, WALLET_AMOUNT, WALLET_PHOTO = range(7)

# ============ کیبوردها ============
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🛒 خرید میو پوینت")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📞 پشتیبانی")],
        [KeyboardButton("🤝 چطور اعتماد کنم"), KeyboardButton("📖 راهنما")]
    ], resize_keyboard=True)

def wallet_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💳 شارژ کیف پول")],
        [KeyboardButton("🔙 بازگشت")]
    ], resize_keyboard=True)

# ============ هندلرها ============
async def start(update, context):
    uid = update.effective_user.id
    get_wallet(uid)
    context.user_data.clear()
    await update.message.reply_text(
        "به ربات خوش آمدید! 👋\nلطفا یک گزینه را انتخاب کنید:",
        reply_markup=main_kb()
    )
    return MENU

async def menu_router(update, context):
    text = update.message.text
    if text == "🛒 خرید میو پوینت":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("بالای 100 میلیون", callback_data="cat_high")],
            [InlineKeyboardButton("پایین 100 میلیون", callback_data="cat_low")]
        ])
        await update.message.reply_text("لطفا یک دسته را انتخاب کنید:", reply_markup=kb)
        return BUY_CAT
    elif text == "💰 کیف پول":
        bal = get_wallet(update.effective_user.id)
        await update.message.reply_text(
            f"💰 موجودی کیف پول شما: {bal:,} تومان",
            reply_markup=wallet_kb()
        )
        return WALLET_MENU
    elif text == "📞 پشتیبانی":
        await update.message.reply_text(
            f"برای ارتباط با پشتیبانی به آیدی زیر پیام دهید:\n{SUPPORT_USERNAME}"
        )
        return MENU
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
    if cat == "high" and amount < THRESHOLD:
        await update.message.reply_text(f"لطفا مقدار بالای {THRESHOLD} میلیون وارد کنید.")
        return BUY_AMOUNT
    if cat == "low" and amount >= THRESHOLD:
        await update.message.reply_text(f"لطفا مقدار پایین {THRESHOLD} میلیون وارد کنید.")
        return BUY_AMOUNT

    price = PRICE_HIGH if cat == "high" else PRICE_LOW
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

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 پرداخت با کیف پول", callback_data="pay_wallet")]
    ])
    await update.message.reply_text(
        f"شماره کارت شما ثبت شد: {card}\n"
        f"مبلغ قابل پرداخت: {total:,} تومان\n\n"
        f"برای پرداخت روی دکمه زیر بزنید:",
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
            f"❌ موجودی کافی نمیباشد.\n"
            f"موجودی شما: {bal:,} تومان\n"
            f"مبلغ لازم: {total:,} تومان\n\n"
            f"لطفا ابتدا از بخش کیف پول، آن را شارژ کنید."
        )
        return MENU

    new_bal = add_wallet(uid, -total)
    amount = context.user_data.get("buy_amount", 0)
    card = context.user_data.get("buy_card", "-")

    admin_text = (
        f"🛒 سفارش جدید میو پوینت\n\n"
        f"🆔 آیدی کاربر: {uid}\n"
        f"👤 نام: {query.from_user.full_name}\n"
        f"💳 شماره کارت میویی: {card}\n"
        f"📊 مقدار: {amount:g} میلیون\n"
        f"💰 مبلغ کل: {total:,} تومان\n"
        f"💼 موجودی باقی‌مانده کاربر: {new_bal:,} تومان"
    )
    try:
        await context.bot.send_message(ADMIN_ID, admin_text)
    except Exception as e:
        logging.error(e)

    await query.edit_message_text(
        f"✅ پرداخت انجام شد.\n"
        f"مبلغ {total:,} تومان از کیف پول شما کسر شد.\n"
        f"موجودی جدید: {new_bal:,} تومان\n\n"
        f"سفارش شما ثبت شد و به زودی میو برای شما واریز می‌شود."
    )
    await context.bot.send_message(uid, "به منوی اصلی بازگشتید 👇", reply_markup=main_kb())
    context.user_data.clear()
    return MENU

async def wallet_charge_start(update, context):
    await update.message.reply_text(
        f"شما در حال کارت به کارت هستید\n"
        f"شماره کارت: {CARD_NUMBER}\n"
        f"بنام {CARD_NAME}\n\n"
        f"لطفا مبلغ پرداخت را وارد کنید:"
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

    c.execute("INSERT INTO pending_charges (user_id, amount, photo_id) VALUES (?, ?, ?)",
              (uid, amount, photo_id))
    conn.commit()
    charge_id = c.lastrowid

    await update.message.reply_text("لطفا منتظر تایید باشید...", reply_markup=main_kb())

    admin_text = (
        f"💳 درخواست شارژ کیف پول\n\n"
        f"🆔 آیدی کاربر: {uid}\n"
        f"👤 نام: {update.effective_user.full_name}\n"
        f"💰 مبلغ: {amount:,} تومان"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"charge_ok_{charge_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"charge_no_{charge_id}")
        ]
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
            await context.bot.send_message(uid,
                f"✅ رسید شما تایید شد و کیف پول شما به مبلغ {amount:,} تومان شارژ شد.")
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
            await context.bot.send_message(uid,
                "❌ متاسفانه رسید شما تایید نشد. برای پیگیری با پشتیبانی تماس بگیرید.")
        except Exception as e:
            logging.error(e)

    await query.answer()

async def back_to_menu(update, context):
    await update.message.reply_text("به منوی اصلی بازگشتید 👇", reply_markup=main_kb())
    return MENU

# ============ ساخت اپلیکیشن ============
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
    },
    fallbacks=[CommandHandler("start", start)],
    allow_reentry=True,
)

app.add_handler(conv, group=0)
app.add_handler(CallbackQueryHandler(charge_callback, pattern="^charge_"), group=1)

# ============ وب‌سرور برای Render ============
async def start_web_server():
    async def handle(request):
        return web.Response(text="Bot is running ✅")
    
    web_app = web.Application()
    web_app.router.add_get('/', handle)
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
