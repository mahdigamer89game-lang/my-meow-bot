import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = "8985946672:AAEE0OGCl9-et8VKhN_PmdGOWzgQ81xduC8"
ADMIN_ID = 1713081033

logging.basicConfig(level=logging.INFO)
user_groups = {}
user_states = {}

def is_admin(uid):
    return uid == ADMIN_ID

def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 گروه‌های من")],
        [KeyboardButton("➕ افزودن گروه جدید")]
    ], resize_keyboard=True)

def groups_kb(uid):
    keyboard = []
    groups = user_groups.get(uid, {})
    for gid, gname in groups.items():
        keyboard.append([KeyboardButton(f"📌 {gname}")])
    keyboard.append([KeyboardButton("🔙 بازگشت")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def actions_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ارسال پیام", callback_data="send_text")],
        [InlineKeyboardButton("🎞 ارسال گیف", callback_data="send_gif")],
        [InlineKeyboardButton("🖼 ارسال عکس", callback_data="send_photo")],
        [InlineKeyboardButton("🎭 ارسال استیکر", callback_data="send_sticker")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    user_states[uid] = None
    await update.message.reply_text("سلام ادمین! به ربات مدیریت گروه خوش آمدی:", reply_markup=main_kb())

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    user_states[uid] = "WAIT_GROUP_ID"
    await update.message.reply_text("لطفا آیدی عددی گروه رو بفرست (مثلاً -1001234567890):\n\n⚠️ برای اینکه بتونم آیدی رو پیدا کنم، ربات باید تو گروه ادمین باشه و یه پیام تو گروه بفرست.")

async def receive_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    if user_states.get(uid) != "WAIT_GROUP_ID": return
    
    gid = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(gid)
        gname = chat.title or f"گروه {gid}"
        if uid not in user_groups:
            user_groups[uid] = {}
        user_groups[uid][gid] = gname
        await update.message.reply_text(f"✅ گروه {gname} با موفقیت ثبت شد.", reply_markup=main_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پیدا کردن گروه: {e}\nمطمئن شو ربات تو گروه ادمینه و آیدی درسته.")
    user_states[uid] = None

async def show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    groups = user_groups.get(uid, {})
    if not groups:
        await update.message.reply_text("هنوز هیچ گروهی ثبت نکردی.", reply_markup=main_kb())
        return
    await update.message.reply_text("یکی از گروه‌های زیر رو انتخاب کن:", reply_markup=groups_kb(uid))

async def handle_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    text = update.message.text
    if text == "🔙 بازگشت":
        user_states[uid] = None
        await update.message.reply_text("به منوی اصلی برگشتی.", reply_markup=main_kb())
        return
    if text.startswith("📌 "):
        gname = text.replace("📌 ", "")
        groups = user_groups.get(uid, {})
        gid = None
        for k, v in groups.items():
            if v == gname:
                gid = k
                break
        if gid:
            user_states[uid] = f"WAIT_ACTION|{gid}"
            await update.message.reply_text(f"برای گروه «{gname}» چه کاری انجام بدم؟", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True))
            await context.bot.send_message(uid, "یکی از گزینه‌های زیر رو انتخاب کن:", reply_markup=actions_kb())

async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid): return
    await query.answer()
    state = user_states.get(uid)
    if not state or not state.startswith("WAIT_ACTION|"): return
    
    gid = state.split("|")[1]
    action = query.data
    
    if action == "send_text":
        user_states[uid] = f"WAIT_CONTENT|{gid}|text"
        await query.edit_message_text("متن پیام رو بفرست:")
    elif action == "send_gif":
        user_states[uid] = f"WAIT_CONTENT|{gid}|gif"
        await query.edit_message_text("گیف (GIF) رو بفرست:")
    elif action == "send_photo":
        user_states[uid] = f"WAIT_CONTENT|{gid}|photo"
        await query.edit_message_text("عکس رو بفرست:")
    elif action == "send_sticker":
        user_states[uid] = f"WAIT_CONTENT|{gid}|sticker"
        await query.edit_message_text("استیکر رو بفرست:")

async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    state = user_states.get(uid)
    if not state or not state.startswith("WAIT_CONTENT|"): return
    
    parts = state.split("|")
    gid = parts[1]
    content_type = parts[2]
    
    try:
        if content_type == "text":
            await context.bot.send_message(chat_id=gid, text=update.message.text)
        elif content_type == "gif":
            await context.bot.send_animation(chat_id=gid, animation=update.message.animation.file_id)
        elif content_type == "photo":
            await context.bot.send_photo(chat_id=gid, photo=update.message.photo[-1].file_id)
        elif content_type == "sticker":
            await context.bot.send_sticker(chat_id=gid, sticker=update.message.sticker.file_id)
        
        await update.message.reply_text("✅ با موفقیت به گروه ارسال شد.", reply_markup=main_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ارسال: {e}")
    
    user_states[uid] = None

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.Regex("^➕ افزودن گروه جدید$"), add_group))
app.add_handler(MessageHandler(filters.Regex("^📋 گروه‌های من$"), show_groups))
app.add_handler(MessageHandler(filters.Regex("^📌 .*$"), handle_group_selection))
app.add_handler(MessageHandler(filters.Regex("^🔙 بازگشت$"), handle_group_selection))
app.add_handler(CallbackQueryHandler(action_callback, pattern="^send_"))
app.add_handler(MessageHandler(filters.TEXT | filters.ANIMATION | filters.PHOTO | filters.Sticker.ALL, receive_content))

app.run_polling()
