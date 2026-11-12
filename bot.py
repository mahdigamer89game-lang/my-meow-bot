Lander 𝟏𝟗:𝟓𝟏:
import asyncio
import logging
import os
import psycopg2
from psycopg2 import pool
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ================= تنظیمات =================
TOKEN = "8985946672:AAEE0OGCl9-et8VKhN_PmdGOWzgQ81xduC8"
ADMIN_ID = 1713081033  # 
DATABASE_URL = os.environ.get("DATABASE_URL")
# ==========================================

logging.basicConfig(level=logging.INFO

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
    db_execute("""CREATE TABLE IF NOT EXISTS user_groups (
        user_id BIGINT,
        group_id TEXT,
        group_name TEXT,
        PRIMARY KEY (user_id, group_id)
    )""")
    db_execute("""CREATE TABLE IF NOT EXISTS states (user_id BIGINT PRIMARY KEY, state TEXT, data TEXT)""")

init_db()

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

# ================= کیبوردها =================
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 گروه‌های من")],
        [KeyboardButton("➕ افزودن گروه جدید")]
    ], resize_keyboard=True)

def groups_kb(uid):
    groups = db_execute_all("SELECT group_id, group_name FROM user_groups WHERE user_id=%s", (uid,))
    keyboard = []
    for gid, gname in groups:
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

# ================= توابع اصلی =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ شما اجازه استفاده از این ربات را ندارید.")
        return
    clear_state(uid)
    await update.message.reply_text("سلام ادمین! به ربات مدیریت گروه خوش آمدی:", reply_markup=main_kb())

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    set_state(uid, "WAIT_GROUP_ID")
    await update.message.reply_text("لطفا آیدی عددی گروه رو بفرست (مثلاً -1001234567890):\n\n⚠️ برای اینکه بتونم آیدی رو پیدا کنم، ربات باید تو گروه ادمین باشه و یه پیام تو گروه بفرست.")

async def receive_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    gid = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(gid)
        gname = chat.title or f"گروه {gid}"
        db_execute("INSERT INTO user_groups (user_id, group_id, group_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (uid, gid, gname))
        await update.message.reply_text(f"✅ گروه {gname} با موفقیت ثبت شد.", reply_markup=main_kb())
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پیدا کردن گروه: {e}\nمطمئن شو ربات تو گروه ادمینه و آیدی درسته.")
    clear_state(uid)

async def show_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    groups = db_execute_all("SELECT group_id, group_name FROM user_groups WHERE user_id=%s", (uid,))
    if not groups:
        await update.message.reply_text("هنوز هیچ گروهی ثبت نکردی.", reply_markup=main_kb())
        return
    await update.message.reply_text("یکی از گروه‌های زیر رو انتخاب کن:", reply_markup=groups_kb(uid))

async def handle_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    text = update.message.text
    if text == "🔙 بازگشت":
        clear_state(uid)
        await update.message.reply_text("به منوی اصلی برگشتی.", reply_markup=main_kb())
        return
    if text.startswith("📌 "):
        gname = text.replace("📌 ", "")
        group = db_execute("SELECT group_id FROM user_groups WHERE user_id=%s AND group_name=%s", (uid, gname), fetch=True)
        if group:
            set_state(uid, "WAIT_ACTION", group[0])
            await update.message.reply_text(f"برای گروه «{gname}» چه کاری انجام بدم؟", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 بازگشت")]], resize_keyboard=True))
            await context.bot.send_message(uid, "یکی از گزینه‌های زیر رو انتخاب کن:", reply_markup=actions_kb())

async def action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid): return
    await query.answer()
    state, gid = get_state(uid)
    if state != "WAIT_ACTION": return
    action = query.data
    if action == "send_text":
        set_state(uid, "WAIT_CONTENT", f"{gid}|text")
        await query.edit_message_text("متن پیام رو بفرست:")
    elif action == "send_gif":
        set_state(uid, "WAIT_CONTENT", f"{gid}|gif")
        await query.edit_message_text("گیف (GIF) رو بفرست:")
    elif action == "send_photo":
        set_state(uid, "WAIT_CONTENT", f"{gid}|photo")
        await query.edit_message_text("عکس رو بفرست:")
    elif action == "send_sticker":
        set_state(uid, "WAIT_CONTENT", f"{gid}|sticker")
        await query.edit_message_text("استیکر رو بفرست:")

async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    state, data = get_state(uid)
    if state != "WAIT_CONTENT": return
    
    gid, content_type = data.split("|")
    
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
    
    clear_state(uid)

# ================= راه‌اندازی =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.Regex("^➕ افزودن گروه جدید$"), add_group))
app.add_handler(MessageHandler(filters.Regex("^📋 گروه‌های من$"), show_groups))
app.add_handler(MessageHandler(filters.Regex("^📌 .*$"), handle_group_selection))
app.add_handler(MessageHandler(filters.Regex("^🔙 بازگشت$"), handle_group_selection))
app.add_handler(CallbackQueryHandler(action_callback, pattern="^send_"))
app.add_handler(MessageHandler(filters.TEXT | filters.ANIMATION | filters.PHOTO | filters.Sticker.ALL, receive_content))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_id))

app.run_polling()
