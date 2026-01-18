import os
import logging
import sqlite3
import asyncio
import time
import threading
import schedule
from flask import Flask
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from claim_coupons import claim_for_token, list_available_coupons, list_my_coupons, list_campaign_calendar, get_today_recommendation

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_FILE = os.getenv("DB_PATH", "users.db")

def init_db():
    db_dir = os.path.dirname(DB_FILE)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logger.info(f"Created database directory: {db_dir}")
        except OSError as e:
            logger.error(f"Failed to create database directory {db_dir}: {e}")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, mcp_token TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    alter_statements = [
        "ALTER TABLE users ADD COLUMN auto_claim_enabled INTEGER DEFAULT 1",
        "ALTER TABLE users ADD COLUMN last_claim_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN last_claim_success INTEGER",
        "ALTER TABLE users ADD COLUMN total_success INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_failed INTEGER DEFAULT 0"
    ]

    for stmt in alter_statements:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass

    c.execute('''CREATE TABLE IF NOT EXISTS accounts
                 (user_id INTEGER, name TEXT, mcp_token TEXT, is_active INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, name))''')

    conn.commit()
    conn.close()

def get_active_account(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, mcp_token FROM accounts WHERE user_id=? AND is_active=1 LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_accounts(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, mcp_token, is_active FROM accounts WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def upsert_account(user_id, name, token, set_active):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO accounts (user_id, name, mcp_token, is_active) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, name) DO UPDATE SET mcp_token=excluded.mcp_token", (user_id, name, token, 1 if set_active else 0))
    if set_active:
        c.execute("UPDATE accounts SET is_active=0 WHERE user_id=? AND name!=?", (user_id, name))
    conn.commit()
    conn.close()

def set_active_account(user_id, name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE accounts SET is_active=1 WHERE user_id=? AND name=?", (user_id, name))
    c.execute("UPDATE accounts SET is_active=0 WHERE user_id=? AND name!=?", (user_id, name))
    conn.commit()
    conn.close()

def get_user_token(user_id):
    active_account = get_active_account(user_id)
    if active_account:
        return active_account[1]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT mcp_token FROM users WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def save_user_token(user_id, username, token):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO users (user_id, username, mcp_token, auto_claim_enabled) VALUES (?, ?, ?, 1) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, mcp_token=excluded.mcp_token", (user_id, username, token))
    conn.commit()
    conn.close()
    upsert_account(user_id, "default", token, True)

def delete_user_token(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, mcp_token FROM users WHERE auto_claim_enabled IS NULL OR auto_claim_enabled=1")
    users = c.fetchall()
    conn.close()
    return users

def set_auto_claim_enabled(user_id, enabled):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET auto_claim_enabled=? WHERE user_id=?", (1 if enabled else 0, user_id))
    conn.commit()
    conn.close()

def get_user_stats_and_status(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT username, auto_claim_enabled, last_claim_at, last_claim_success, total_success, total_failed, created_at "
        "FROM users WHERE user_id=?",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row

def update_claim_stats(user_id, success):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET last_claim_at=CURRENT_TIMESTAMP, last_claim_success=?, "
        "total_success=COALESCE(total_success,0)+?, total_failed=COALESCE(total_failed,0)+? "
        "WHERE user_id=?",
        (1 if success else 0, 1 if success else 0, 0 if success else 1, user_id)
    )
    conn.commit()
    conn.close()

def get_admin_summary():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM users WHERE auto_claim_enabled IS NULL OR auto_claim_enabled=1")
    auto_users = c.fetchone()[0] or 0
    c.execute("SELECT COALESCE(SUM(total_success),0), COALESCE(SUM(total_failed),0) FROM users")
    row = c.fetchone()
    total_success = row[0] if row and row[0] is not None else 0
    total_failed = row[1] if row and row[1] is not None else 0
    conn.close()
    return total_users, auto_users, total_success, total_failed

# Bot Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 欢迎使用麦当劳自动领券 Bot！\n\n"
        "请先发送你的 MCP Token 给我完成绑定。\n"
        "获取地址：https://open.mcd.cn/mcp/console\n\n"
        "常用命令：\n"
        "/claim - 立即领券\n"
        "/coupons - 查看当前可领优惠券\n"
        "/mycoupons - 查看你已拥有的优惠券\n"
        "/calendar - 查看活动日历\n"
        "/today - 今日智能用券建议\n"
        "/status - 查看当前状态\n"
        "/stats - 查看领券统计\n"
        "/autoclaim on/off - 开启或关闭每日自动领券\n"
        "/account add/use/list/del - 多账号管理\n"
        "/unbind - 解除绑定\n"
        "/admin - 管理员总览\n"
        "/help - 查看帮助"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "使用说明：\n"
        "1. 先在 https://open.mcd.cn/mcp/console 获取你的 MCP Token。\n"
        "2. 将 Token 直接发送给我完成绑定。\n"
        "3. 绑定后，我会在每天 10:30 自动帮你领券。\n\n"
        "常用命令：\n"
        "/claim - 立即领券\n"
        "/coupons - 查看当前可领优惠券\n"
        "/mycoupons - 查看你已拥有的优惠券\n"
        "/calendar - 查看活动日历\n"
        "/today - 今日智能用券建议\n"
        "/status - 查看当前状态\n"
        "/stats - 查看领券统计\n"
        "/autoclaim on/off - 开启或关闭每日自动领券\n"
        "/account add/use/list/del - 多账号管理\n"
        "/unbind - 解除绑定\n"
        "/admin - 管理员总览"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username

    if len(text) > 20 and not text.startswith('/'):
        await update.message.reply_text("🔍 正在验证你的 Token，请稍等...")
        
        result = await claim_for_token(text, enable_push=False)
        
        if "Error" in result and "tool not found" not in result and "Execution Result" not in result:
             await update.message.reply_text(f"❌ Token 无效或连接失败。\n{result}")
        else:
            save_user_token(user_id, username, text)
            await update.message.reply_text(
                f"✅ Token 验证成功并已保存！\n\n"
                f"我已经帮你执行了一次领券：\n{result}\n\n"
                f"之后我会在每天 10:30 自动为你领券。"
            )
    else:
        await update.message.reply_text("❓ 没看懂，你可以直接把 MCP Token 发给我完成绑定。")

async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    await update.message.reply_text("🍟 正在为你领券...")
    result = await claim_for_token(token, enable_push=False)
    success = True
    lower = result.lower()
    if "error" in lower or "401" in result or "unauthorized" in lower:
        success = False
    update_claim_stats(user_id, success)
    await update.message.reply_text(f"完成！\n{result}")

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    args = context.args
    date = args[0] if args else None
    await update.message.reply_text("🗓️ 正在为你查询活动日历，请稍等...")
    result = await list_campaign_calendar(token, date)
    await update.message.reply_text(result or "暂未查询到活动信息。")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    await update.message.reply_text("🤖 正在结合活动日历和可领优惠券为你生成今天的用券建议，请稍等...")
    result = await get_today_recommendation(token)
    await update.message.reply_text(result)

async def coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    await update.message.reply_text("📋 正在为你查询当前可领优惠券，请稍等...")
    result = await list_available_coupons(token)
    await update.message.reply_text(result or "暂无可领优惠券。")

async def my_coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    await update.message.reply_text("🎟️ 正在为你查询你已拥有的优惠券，请稍等...")
    result = await list_my_coupons(token)
    await update.message.reply_text(result or "暂未查询到你的优惠券。")

async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    delete_user_token(user_id)
    await update.message.reply_text("🗑️ 已删除你的 Token，我将不再自动为你领券。")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    row = get_user_stats_and_status(user_id)

    if not token or not row:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    username, auto_claim_enabled, last_claim_at, last_claim_success, total_success, total_failed, created_at = row

    auto_enabled = True
    if auto_claim_enabled is not None and auto_claim_enabled == 0:
        auto_enabled = False

    if last_claim_success is None:
        last_result_text = "暂无记录"
    elif last_claim_success == 1:
        last_result_text = "成功"
    else:
        last_result_text = "失败"

    msg = (
        "📊 当前账号状态：\n\n"
        f"用户：@{username or '未知'}（ID: {user_id}）\n"
        "绑定状态：已绑定\n"
        f"自动领券：{'已开启' if auto_enabled else '已关闭'}\n"
        f"上次领券时间：{last_claim_at or '暂无记录'}\n"
        f"上次结果：{last_result_text}\n"
    )

    await update.message.reply_text(msg)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    row = get_user_stats_and_status(user_id)

    if not token or not row:
        await update.message.reply_text("⚠️ 暂无数据，你还没有绑定 MCP Token 或从未领过券。")
        return

    _, _, _, _, total_success, total_failed, _ = row

    success_count = total_success or 0
    failed_count = total_failed or 0
    total = success_count + failed_count

    # Gamification Logic
    title = "🍔 麦当劳路人"
    if success_count >= 10:
        title = "🍟 麦门新徒"
    if success_count >= 50:
        title = "〽️ 金拱门长老"
    if success_count >= 100:
        title = "👑 麦当劳股东"
    
    # Lucky/Unlucky Logic
    luck_status = ""
    if total > 5 and failed_count > success_count:
        luck_status = "\n(运势：😱 非酋附体，建议洗手)"
    elif total > 5 and failed_count == 0:
        luck_status = "\n(运势：✨ 欧皇降临)"

    msg = (
        "📈 你的领券统计：\n\n"
        f"当前称号：{title}\n"
        f"总尝试次数：{total}\n"
        f"成功次数：{success_count}\n"
        f"失败次数：{failed_count}{luck_status}\n"
    )

    await update.message.reply_text(msg)

async def autoclaim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    args = context.args
    row = get_user_stats_and_status(user_id)

    if not args:
        auto_claim_enabled = None
        if row:
            _, auto_claim_enabled, _, _, _, _, _ = row
        enabled = True
        if auto_claim_enabled is not None and auto_claim_enabled == 0:
            enabled = False
        msg = (
            f"当前自动领券状态：{'已开启' if enabled else '已关闭'}\n"
            "使用方式：/autoclaim on 开启，/autoclaim off 关闭。"
        )
        await update.message.reply_text(msg)
        return

    mode = args[0].lower()
    enable_values = ["on", "开启", "开", "true", "1"]
    disable_values = ["off", "关闭", "关", "false", "0"]

    if mode in enable_values:
        set_auto_claim_enabled(user_id, True)
        await update.message.reply_text("✅ 已开启每日自动领券。")
    elif mode in disable_values:
        set_auto_claim_enabled(user_id, False)
        await update.message.reply_text("✅ 已关闭每日自动领券，你仍然可以使用 /claim 手动领券。")
    else:
        await update.message.reply_text("❓ 无法识别参数，请使用 /autoclaim on 或 /autoclaim off。")

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    args = context.args
    if not args:
        await update.message.reply_text("用法：/account add <名称> <Token>，/account use <名称>，/account list，/account del <名称>")
        return
    sub = args[0].lower()
    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("用法：/account add <名称> <Token>")
            return
        name = args[1]
        new_token = " ".join(args[2:])
        upsert_account(user_id, name, new_token, True)
        save_user_token(user_id, update.effective_user.username, new_token)
        await update.message.reply_text(f"✅ 已添加/更新账号 {name} 并设为当前账号。")
    elif sub == "use":
        if len(args) < 2:
            await update.message.reply_text("用法：/account use <名称>")
            return
        name = args[1]
        accounts = get_accounts(user_id)
        target = None
        for acc in accounts:
            if acc[0] == name:
                target = acc
                break
        if not target:
            await update.message.reply_text("未找到该账号名称。")
            return
        set_active_account(user_id, name)
        save_user_token(user_id, update.effective_user.username, target[1])
        await update.message.reply_text(f"✅ 已切换到账号 {name}。")
    elif sub == "list":
        accounts = get_accounts(user_id)
        if not accounts:
            await update.message.reply_text("你还没有添加任何账号。")
            return
        lines = []
        for name, acc_token, is_active in accounts:
            mark = "✅" if is_active else "•"
            lines.append(f"{mark} {name}")
        await update.message.reply_text("你的账号列表：\n" + "\n".join(lines))
    elif sub == "del":
        if len(args) < 2:
            await update.message.reply_text("用法：/account del <名称>")
            return
        name = args[1]
        accounts = get_accounts(user_id)
        exists = False
        was_active = False
        for acc_name, acc_token, is_active in accounts:
            if acc_name == name:
                exists = True
                if is_active:
                    was_active = True
                break
        if not exists:
            await update.message.reply_text("未找到该账号名称。")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM accounts WHERE user_id=? AND name=?", (user_id, name))
        conn.commit()
        conn.close()
        if was_active:
            remaining = get_accounts(user_id)
            if remaining:
                first_name, first_token, _ = remaining[0]
                set_active_account(user_id, first_name)
                save_user_token(user_id, update.effective_user.username, first_token)
            else:
                delete_user_token(user_id)
        await update.message.reply_text(f"✅ 已删除账号 {name}。")
    else:
        await update.message.reply_text("未知子命令，用法：/account add/use/list/del")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    admin_chat_id = os.getenv("TG_CHAT_ID")

    if not admin_chat_id:
        await update.message.reply_text("⚠️ 未配置管理员 TG_CHAT_ID，无法使用 /admin。")
        return

    try:
        admin_id_int = int(admin_chat_id)
    except ValueError:
        await update.message.reply_text("⚠️ 管理员配置无效，请检查 TG_CHAT_ID。")
        return

    if user_id != admin_id_int:
        await update.message.reply_text("⛔ 只有管理员可以使用此命令。")
        return

    args = context.args
    if args and args[0].lower() == "sweep":
        application = context.application
        application.create_task(scheduled_job(application))
        await update.message.reply_text("🚀 已开始执行一次全量自动领券任务。")
        return

    total_users, auto_users, total_success, total_failed = get_admin_summary()

    msg = (
        "🧾 管理员总览：\n\n"
        f"注册用户数：{total_users}\n"
        f"开启自动领券的用户数：{auto_users}\n"
        f"累计成功次数：{total_success}\n"
        f"累计失败次数：{total_failed}\n"
    )

    await update.message.reply_text(msg)

# Scheduler logic
from quotes import MCD_QUOTES
import random

async def process_user_claim(application: Application, user_id, token, semaphore):
    async with semaphore:
        try:
            logger.info(f"Claiming for user {user_id}")
            result = await claim_for_token(token, enable_push=False)
            success = True
            lower = result.lower()
            if "error" in lower or "401" in result or "unauthorized" in lower:
                success = False
            update_claim_stats(user_id, success)

            message = f"🔔 每日自动领券结果：\n\n{result}"

            if "error" in lower or "401" in result or "unauthorized" in lower:
                message += "\n\n⚠️ 注意：你的 Token 可能已失效或无效，请重新发送新的 Token 进行绑定。"
            elif success:
                # Add random quote for successful claims
                quote = random.choice(MCD_QUOTES)
                message += f"\n\n🍟 {quote}"

            await application.bot.send_message(chat_id=user_id, text=message)
        except Exception as e:
            logger.error(f"Failed to auto-claim for user {user_id}: {e}")

async def scheduled_job(application: Application):
    logger.info("Running scheduled daily claim for all users...")
    users = get_all_users()
    
    # Limit concurrency to 5 to avoid overwhelming resources
    semaphore = asyncio.Semaphore(5)
    tasks = []
    
    for user_id, token in users:
        tasks.append(process_user_claim(application, user_id, token, semaphore))
    
    await asyncio.gather(*tasks)
    logger.info("Scheduled run complete.")

def run_scheduler(application, loop):
    """
    Runs the schedule in a separate thread.
    """
    logger.info("Scheduler thread started")
    
    def job_wrapper():
        asyncio.run_coroutine_threadsafe(scheduled_job(application), loop)

    # Schedule daily at 10:30
    schedule.every().day.at("10:30").do(job_wrapper)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# Keep-alive web server for PaaS (Koyeb/Render/HF Spaces)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "McDonald's Coupon Bot is running! 🍔"

def run_flask():
    port = int(os.environ.get("PORT", 7860))
    app.run(host='0.0.0.0', port=port)

def main():
    token = os.getenv("TG_BOT_TOKEN")
    if not token:
        print("Error: TG_BOT_TOKEN not found in .env")
        return

    init_db()
    
    # Auto-register owner if env vars are present
    owner_token = os.getenv("MCD_MCP_TOKEN")
    owner_chat_id = os.getenv("TG_CHAT_ID")
    if owner_token and owner_chat_id:
        try:
            chat_id = int(owner_chat_id)
            save_user_token(chat_id, "Owner", owner_token)
            logger.info(f"Auto-registered owner (ID: {chat_id}) from environment variables.")
        except ValueError:
            logger.warning("TG_CHAT_ID is not a valid integer, skipping owner auto-registration.")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("claim", claim_command))
    application.add_handler(CommandHandler("calendar", calendar_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("coupons", coupons_command))
    application.add_handler(CommandHandler("mycoupons", my_coupons_command))
    application.add_handler(CommandHandler("account", account_command))
    application.add_handler(CommandHandler("unbind", unbind_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("autoclaim", autoclaim_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the scheduler in a background thread
    # We need to pass the event loop to the thread so it can schedule async tasks back to the main loop
    loop = asyncio.get_event_loop()
    threading.Thread(target=run_scheduler, args=(application, loop), daemon=True).start()

    # Start Flask server in a background thread
    threading.Thread(target=run_flask, daemon=True).start()

    print("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()
