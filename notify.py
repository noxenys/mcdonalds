import os
import httpx
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

MAX_PUSH_MESSAGE_LENGTH = 3500


def _shorten_message(message: str, limit: int = MAX_PUSH_MESSAGE_LENGTH) -> str:
    if not message:
        return ""
    text = str(message)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _request_with_retry(method: str, url: str, **kwargs):
    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp


async def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    base_payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        await _request_with_retry("POST", url, json=base_payload)
        logger.info("Telegram notification sent successfully.")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 400:
            payload = {
                "chat_id": chat_id,
                "text": message,
            }
            try:
                await _request_with_retry("POST", url, json=payload)
                logger.info("Telegram notification sent successfully without parse_mode.")
            except Exception as e2:
                logger.error(f"Failed to send Telegram notification in plain text: {e2}")
        else:
            logger.error(f"Failed to send Telegram notification: {e}")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


async def send_bark(key, message):
    url = f"https://api.day.app/{key}/McDonalds_Coupon/{message}"
    try:
        await _request_with_retry("GET", url)
        logger.info("Bark notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Bark notification: {e}")


async def send_feishu(webhook, message):
    payload = {
        "msg_type": "text",
        "content": {
            "text": message,
        },
    }
    try:
        await _request_with_retry("POST", webhook, json=payload)
        logger.info("Feishu notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Feishu notification: {e}")


async def send_serverchan(sendkey, message):
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = {
        "title": "McDonalds Coupon Report",
        "desp": message,
    }
    try:
        await _request_with_retry("POST", url, data=payload)
        logger.info("ServerChan notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send ServerChan notification: {e}")


async def push_all(message):
    text = _shorten_message(message)
    tasks = []

    tg_token = os.getenv("TG_BOT_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    if tg_token and tg_chat_id:
        tasks.append(send_telegram(tg_token, tg_chat_id, text))

    bark_key = os.getenv("BARK_KEY")
    if bark_key:
        tasks.append(send_bark(bark_key, text))

    feishu_webhook = os.getenv("FEISHU_WEBHOOK")
    if feishu_webhook:
        tasks.append(send_feishu(feishu_webhook, text))

    serverchan_key = os.getenv("SERVERCHAN_SENDKEY")
    if serverchan_key:
        tasks.append(send_serverchan(serverchan_key, text))

    if tasks:
        await asyncio.gather(*tasks)
    else:
        logger.info("No notification services configured. Skipping push.")


if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(push_all("Test message from McDonalds Script"))

def get_active_account(user_id):
    session = get_db()
    try:
        account = session.query(Account).filter(Account.user_id == user_id, Account.is_active == 1).first()
        if account:
            return (account.name, account.mcp_token)
        return None
    finally:
        session.close()

def get_accounts(user_id):
    session = get_db()
    try:
        accounts = session.query(Account).filter(Account.user_id == user_id).all()
        return [(acc.name, acc.mcp_token, acc.is_active) for acc in accounts]
    finally:
        session.close()

def upsert_account(user_id, name, token, set_active):
    session = get_db()
    try:
        account = session.query(Account).filter(Account.user_id == user_id, Account.name == name).first()
        if account:
            account.mcp_token = token
            if set_active:
                # Deactivate others
                session.query(Account).filter(Account.user_id == user_id).update({"is_active": 0})
                account.is_active = 1
        else:
            if set_active:
                session.query(Account).filter(Account.user_id == user_id).update({"is_active": 0})
            account = Account(user_id=user_id, name=name, mcp_token=token, is_active=1 if set_active else 0)
            session.add(account)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in upsert_account: {e}")
    finally:
        session.close()

def set_active_account(user_id, name):
    session = get_db()
    try:
        session.query(Account).filter(Account.user_id == user_id).update({"is_active": 0})
        session.query(Account).filter(Account.user_id == user_id, Account.name == name).update({"is_active": 1})
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in set_active_account: {e}")
    finally:
        session.close()

def get_user_token(user_id):
    active_account = get_active_account(user_id)
    if active_account:
        return active_account[1]
    
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        return user.mcp_token if user else None
    finally:
        session.close()

def save_user_token(user_id, username, token):
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            user.username = username
            user.mcp_token = token
            # Ensure auto_claim is enabled if it was null (though default handles it)
        else:
            user = User(user_id=user_id, username=username, mcp_token=token, auto_claim_enabled=1)
            session.add(user)
        session.commit()
        
        # Also sync to default account
        upsert_account(user_id, "default", token, True)
    except Exception as e:
        session.rollback()
        logger.error(f"Error in save_user_token: {e}")
    finally:
        session.close()

def delete_user_token(user_id):
    session = get_db()
    try:
        session.query(User).filter(User.user_id == user_id).delete()
        session.query(Account).filter(Account.user_id == user_id).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in delete_user_token: {e}")
    finally:
        session.close()

def get_all_users():
    session = get_db()
    try:
        # auto_claim_enabled IS NULL OR auto_claim_enabled=1
        users = session.query(User).filter((User.auto_claim_enabled == None) | (User.auto_claim_enabled == 1)).all()
        return [(u.user_id, u.mcp_token, u.claim_report_enabled) for u in users]
    finally:
        session.close()

def set_auto_claim_enabled(user_id, enabled):
    session = get_db()
    try:
        val = 1 if enabled else 0
        session.query(User).filter(User.user_id == user_id).update({"auto_claim_enabled": val})
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in set_auto_claim_enabled: {e}")
    finally:
        session.close()

def set_claim_report_enabled(user_id, enabled):
    session = get_db()
    try:
        val = 1 if enabled else 0
        session.query(User).filter(User.user_id == user_id).update({"claim_report_enabled": val})
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in set_claim_report_enabled: {e}")
    finally:
        session.close()

def get_user_stats_and_status(user_id):
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            return (user.username, user.auto_claim_enabled, user.claim_report_enabled, user.last_claim_at, 
                    user.last_claim_success, user.total_success, user.total_failed, user.created_at)
        return None
    finally:
        session.close()

def update_claim_stats(user_id, success):
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            user.last_claim_at = func.now()
            user.last_claim_success = 1 if success else 0
            user.total_success = (user.total_success or 0) + (1 if success else 0)
            user.total_failed = (user.total_failed or 0) + (0 if success else 1)
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in update_claim_stats: {e}")
    finally:
        session.close()

def get_admin_summary():
    session = get_db()
    try:
        total_users = session.query(User).count()
        auto_users = session.query(User).filter((User.auto_claim_enabled == None) | (User.auto_claim_enabled == 1)).count()
        
        result = session.query(
            func.sum(User.total_success),
            func.sum(User.total_failed)
        ).first()
        
        total_success = result[0] or 0
        total_failed = result[1] or 0
        
        return total_users, auto_users, int(total_success), int(total_failed)
    finally:
        session.close()

# Bot Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        ["🍟 立即领券", "📅 今日推荐"],
        ["🎟️ 我的券包", "📜 可领列表"],
        ["📊 领券统计", "⚙️ 账号管理", "ℹ️ 帮助/状态"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "👋 欢迎使用麦当劳自动领券 Bot！\n\n"
        "请先发送你的 MCP Token 给我完成绑定。\n"
        "获取地址：https://open.mcd.cn/mcp/console\n\n"
        "你可以直接使用底部的菜单按钮，也可以使用以下命令：\n"
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
        "/help - 查看帮助",
        reply_markup=reply_markup
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for /start to show the menu."""
    await start(update, context)

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    username = update.effective_user.username
    args = context.args
    
    if not args:
        await update.message.reply_text("用法：/token <你的MCP Token>\n\n你可以直接把 Token 发给我，或者使用此命令设置。")
        return

    token = args[0]
    if len(token) < 20:
        await update.message.reply_text("❌ Token 看起来太短了，请检查是否正确。")
        return

    await update.message.reply_text("🔍 正在验证你的 Token，请稍等...")
    
    # Reuse verification logic
    result = await claim_for_token(token, enable_push=False)
    
    if "Error" in result and "tool not found" not in result and "Execution Result" not in result:
         await update.message.reply_text(f"❌ Token 无效或连接失败。\n{result}")
    else:
        save_user_token(user_id, username, token)
        await update.message.reply_text(
            f"✅ Token 验证成功并已保存！\n\n"
            f"我已经帮你执行了一次领券：\n{result}\n\n"
            f"之后我会在每天 10:30 自动为你领券。"
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

    # Handle Menu Buttons
    if text == "🍟 立即领券":
        await claim_command(update, context)
        return
    elif text == "📅 今日推荐":
        await today_command(update, context)
        return
    elif text == "🎟️ 我的券包":
        await my_coupons_command(update, context)
        return
    elif text == "📜 可领列表":
        await coupons_command(update, context)
        return
    elif text == "📊 领券统计":
        await stats_command(update, context)
        return
    elif text == "⚙️ 账号管理":
        await account_command(update, context)
        return
    elif text == "ℹ️ 帮助/状态":
        await status_command(update, context)
        return

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
    await update.message.reply_text(f"完成！\n{result}", parse_mode='Markdown')

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    args = context.args
    date = args[0] if args else None
    await update.message.reply_text("🗓️ 正在为你查询活动日历，请稍等...")

    # typing feedback
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    text_result = await list_campaign_calendar(token, date, return_raw=False)
    if not text_result:
        await update.message.reply_text("暂未查询到活动信息。")
    else:
        # Telegraph 图文页（成功则仅发摘要+链接）
        page_url = None
        try:
            page_url = await telegraph_service.create_page(
                title=f"活动日历",
                content_nodes=build_telegraph_nodes_from_text(text_result, title=f"活动日历")
            )
        except Exception as e:
            logger.error(f"Telegraph page error: {e}")
        summary = sanitize_text(text_result)[:300] + ("..." if len(text_result) > 300 else "")
        if page_url:
            await update.message.reply_text(f"📄 活动日历（图文版）：{page_url}\n\n{summary}", disable_web_page_preview=True)
        else:
            await send_chunked(update, sanitize_text(text_result), parse_mode=None)

def sanitize_text(text: str) -> str:
    if not text:
        return ""
    # 移除图片标签和裸链接
    cleaned_lines = []
    for line in text.splitlines():
        l = line.strip()
        if not l:
            continue
        if l.startswith("http") or "<img" in l:
            continue
        # 去掉常见 HTML 标签
        l = re.sub(r"<[^>]+>", "", l)
        # 去掉多余的反斜杠
        l = l.replace("\\", "")
        cleaned_lines.append(l)
    cleaned = "\n".join(cleaned_lines)
    # 避免 Markdown 特殊字符影响，统一发送纯文本（不设置 parse_mode）
    # 但仍可简单规范标题符号
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    return cleaned

# Helper: chunked send without Update context (used in scheduler)
async def send_chunked_direct(bot, chat_id: int, text: str, chunk_size: int = 3500):
    if not text:
        return
    parts = []
    buf = ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > chunk_size:
            parts.append(buf)
            buf = ""
        buf = (buf + "\n" + line).strip()
    if buf:
        parts.append(buf)
    final_parts = []
    for p in parts:
        if len(p) <= chunk_size:
            final_parts.append(p)
        else:
            for i in range(0, len(p), chunk_size):
                final_parts.append(p[i:i+chunk_size])
    for p in final_parts:
        try:
            await bot.send_message(chat_id=chat_id, text=p, disable_web_page_preview=True)
        except Exception:
            await bot.send_message(chat_id=chat_id, text="[消息发送失败片段已省略]", disable_web_page_preview=True)

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    await update.message.reply_text("🤖 正在结合活动日历和可领优惠券为你生成今天的用券建议，请稍等...")
    # typing feedback
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    try:
        result = await asyncio.wait_for(get_today_recommendation(token), timeout=40)
        # Telegraph 图文页（成功仅发摘要+链接）
        page_url = None
        try:
            page_url = await telegraph_service.create_page(
                title=f"今日推荐",
                content_nodes=build_telegraph_nodes_from_text(result, title=f"今日推荐")
            )
        except Exception as e:
            logger.error(f"Telegraph page error: {e}")
        summary = sanitize_text(result)[:300] + ("..." if len(result) > 300 else "")
        if page_url:
            await update.message.reply_text(f"📄 今日推荐（图文版）：{page_url}\n\n{summary}", disable_web_page_preview=True)
        else:
            await send_chunked(update, sanitize_text(result), parse_mode=None)
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "⏰ 今日推荐生成超时，可能是麦当劳 MCP 服务响应过慢。\n"
            "你可以先使用 /coupons 和 /calendar 单独查看，稍后再试 /today。"
        )

async def coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    await update.message.reply_text("📋 正在为你查询当前可领优惠券，请稍等...")
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    result = await list_available_coupons(token)
    if not result:
        await update.message.reply_text("暂无可领优惠券。")
    else:
        page_url = None
        try:
            page_url = await telegraph_service.create_page(
                title=f"可领优惠券",
                content_nodes=build_telegraph_nodes_from_text(result, title=f"可领优惠券")
            )
        except Exception as e:
            logger.error(f"Telegraph page error (coupons): {e}")
        summary = sanitize_text(result)[:300] + ("..." if len(result) > 300 else "")
        if page_url:
            await update.message.reply_text(f"📄 可领优惠券（图文版）：{page_url}\n\n{summary}", disable_web_page_preview=True)
        else:
            await send_chunked(update, sanitize_text(result), parse_mode=None)

async def my_coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    await update.message.reply_text("🎟️ 正在为你查询你已拥有的优惠券，请稍等...")
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    result = await list_my_coupons(token)
    if not result:
        await update.message.reply_text("暂未查询到你的优惠券。")
    else:
        page_url = None
        try:
            page_url = await telegraph_service.create_page(
                title=f"我的优惠券",
                content_nodes=build_telegraph_nodes_from_text(result, title=f"我的优惠券")
            )
        except Exception as e:
            logger.error(f"Telegraph page error (mycoupons): {e}")
        summary = sanitize_text(result)[:300] + ("..." if len(result) > 300 else "")
        if page_url:
            await update.message.reply_text(f"📄 我的优惠券（图文版）：{page_url}\n\n{summary}", disable_web_page_preview=True)
        else:
            await send_chunked(update, sanitize_text(result), parse_mode=None)

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

    username, auto_claim_enabled, claim_report_enabled, last_claim_at, last_claim_success, total_success, total_failed, created_at = row

    auto_enabled = True
    if auto_claim_enabled is not None and auto_claim_enabled == 0:
        auto_enabled = False

    report_enabled = True
    if claim_report_enabled is not None and claim_report_enabled == 0:
        report_enabled = False

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
        f"自动领券：{'✅ 已开启' if auto_enabled else '🚫 已关闭'}\n"
        f"领券汇报：{'✅ 已开启' if report_enabled else '🚫 已关闭'}\n"
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

    _, _, _, _, _, total_success, total_failed, _ = row

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
            _, auto_claim_enabled, _, _, _, _, _, _ = row
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

async def autoclaimreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    args = context.args
    row = get_user_stats_and_status(user_id)
    
    # row = (username, auto_claim_enabled, claim_report_enabled, ...)
    # Wait, I updated get_user_stats_and_status to return 8 items.
    # I need to verify unpacking.
    
    if not args:
        report_enabled = None
        if row:
            # Need to carefully unpack
             _, _, report_enabled, _, _, _, _, _ = row
        
        enabled = True
        if report_enabled is not None and report_enabled == 0:
            enabled = False
        
        msg = (
            f"当前自动领券汇报状态：{'✅ 开启' if enabled else '🚫 关闭'}\n"
            "开启后，每天自动领券无论成功或失败都会发送消息通知。\n"
            "使用方式：/autoclaimreport on 开启，/autoclaimreport off 关闭。"
        )
        await update.message.reply_text(msg)
        return

    mode = args[0].lower()
    enable_values = ["on", "开启", "开", "true", "1"]
    disable_values = ["off", "关闭", "关", "false", "0"]

    if mode in enable_values:
        set_claim_report_enabled(user_id, True)
        await update.message.reply_text("✅ 已开启自动领券汇报。")
    elif mode in disable_values:
        set_claim_report_enabled(user_id, False)
        await update.message.reply_text("✅ 已关闭自动领券汇报。")
    else:
        await update.message.reply_text("❓ 无法识别参数，请使用 /autoclaimreport on 或 /autoclaimreport off。")

async def cleartoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias for unbind but emphasizes clearing all data."""
    await unbind_command(update, context)

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    args = context.args
    if not args:
        msg = (
            "👤 *多账号管理*\n\n"
            "你可以同时绑定多个麦当劳账号，并随时切换。\n\n"
            "📋 *命令列表*：\n"
            "`/account add <名称> <Token>` - 添加新账号\n"
            "`/account use <名称>` - 切换到指定账号\n"
            "`/account list` - 查看已添加的账号\n"
            "`/account del <名称>` - 删除指定账号\n"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
        return
    sub = args[0].lower()
    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("❌ 格式错误\n请使用：`/account add <名称> <Token>`", parse_mode='Markdown')
            return
        name = args[1]
        new_token = " ".join(args[2:])
        if len(new_token) < 20:
             await update.message.reply_text("❌ Token 无效或太短，请检查。", parse_mode='Markdown')
             return
        
        # Verify token validity before adding
        await update.message.reply_text(f"🔍 正在验证账号 `{name}` 的 Token...", parse_mode='Markdown')
        result = await claim_for_token(new_token, enable_push=False)
        
        if "Error" in result and "tool not found" not in result and "Execution Result" not in result:
             await update.message.reply_text(f"❌ Token 验证失败，账号未添加。\n错误信息：{result}")
             return

        upsert_account(user_id, name, new_token, True)
        save_user_token(user_id, update.effective_user.username, new_token)
        await update.message.reply_text(f"✅ 账号 `{name}` 添加成功并设为当前账号！", parse_mode='Markdown')
        
    elif sub == "use":
        if len(args) < 2:
            await update.message.reply_text("❌ 格式错误\n请使用：`/account use <名称>`", parse_mode='Markdown')
            return
        name = args[1]
        accounts = get_accounts(user_id)
        target = None
        for acc in accounts:
            if acc[0] == name:
                target = acc
                break
        if not target:
            await update.message.reply_text(f"❌ 未找到名为 `{name}` 的账号。", parse_mode='Markdown')
            return
        set_active_account(user_id, name)
        save_user_token(user_id, update.effective_user.username, target[1])
        await update.message.reply_text(f"✅ 已切换到账号 `{name}`。", parse_mode='Markdown')
        
    elif sub == "list":
        accounts = get_accounts(user_id)
        if not accounts:
            await update.message.reply_text("⚠️ 你还没有添加任何账号。")
            return
        lines = []
        for name, acc_token, is_active in accounts:
            mark = "✅" if is_active else "⚪️"
            lines.append(f"{mark} `{name}`")
        await update.message.reply_text("📋 **你的账号列表**：\n\n" + "\n".join(lines), parse_mode='Markdown')
        
    elif sub == "del":
        if len(args) < 2:
            await update.message.reply_text("❌ 格式错误\n请使用：`/account del <名称>`", parse_mode='Markdown')
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
            await update.message.reply_text(f"❌ 未找到名为 `{name}` 的账号。", parse_mode='Markdown')
            return
        
        # Use SQLAlchemy session for deletion to be safe
        session = get_db()
        try:
            session.query(Account).filter(Account.user_id == user_id, Account.name == name).delete()
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting account: {e}")
            await update.message.reply_text("❌ 删除失败，数据库错误。")
            return
        finally:
            session.close()

        if was_active:
            remaining = get_accounts(user_id)
            if remaining:
                first_name, first_token, _ = remaining[0]
                set_active_account(user_id, first_name)
                save_user_token(user_id, update.effective_user.username, first_token)
                await update.message.reply_text(f"✅ 已删除账号 `{name}`。\n自动切换到 `{first_name}`。", parse_mode='Markdown')
            else:
                delete_user_token(user_id)
                await update.message.reply_text(f"✅ 已删除账号 `{name}`。\n你当前没有绑定任何账号。", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ 已删除账号 `{name}`。", parse_mode='Markdown')
            
    else:
        await update.message.reply_text("❓ 未知子命令，请直接输入 `/account` 查看帮助。")

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
    
    if args and args[0].lower() == "broadcast":
        if len(args) < 2:
            await update.message.reply_text("⚠️ 用法：/admin broadcast <消息内容>")
            return
        
        message = " ".join(args[1:])
        users = get_all_users()
        count = 0
        
        await update.message.reply_text(f"📣 正在向 {len(users)} 位用户发送广播...")
        
        for uid, _, _ in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 管理员通知：\n\n{message}")
                count += 1
                await asyncio.sleep(0.1) # Avoid flooding
            except Exception as e:
                logger.error(f"Failed to broadcast to {uid}: {e}")
                
        await update.message.reply_text(f"✅ 广播完成，成功发送给 {count} 位用户。")
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

async def process_user_claim(application: Application, user_id, token, report_enabled, semaphore):
    async with semaphore:
        try:
            logger.info(f"Claiming for user {user_id}")
            result = await claim_for_token(token, enable_push=False)
            success = True
            lower = result.lower()
            if "error" in lower or "401" in result or "unauthorized" in lower:
                success = False
            update_claim_stats(user_id, success)

            # Only send message if report_enabled is True (default 1) or None (treated as True)
            if report_enabled is None or report_enabled == 1:
                message = f"🔔 每日自动领券结果：\n\n{result}"

                if "error" in lower or "401" in result or "unauthorized" in lower:
                    message += "\n\n⚠️ 注意：你的 Token 可能已失效或无效，请重新发送新的 Token 进行绑定。"
                    try:
                        mark_token_invalid_pause(user_id)
                    except Exception as e:
                        logger.error(f"Failed to mark token invalid for {user_id}: {e}")
                elif success:
                    # Add random quote for successful claims
                    quote = random.choice(MCD_QUOTES)
                    message += f"\n\n🍟 {quote}"

                await application.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to auto-claim for user {user_id}: {e}")

async def scheduled_job(application: Application):
    logger.info("Running scheduled daily claim for all users...")
    users = get_all_users()
    
    # Limit concurrency to 5 to avoid overwhelming resources
    semaphore = asyncio.Semaphore(5)
    tasks = []
    
    for user_id, token, report_enabled in users:
        tasks.append(process_user_claim(application, user_id, token, report_enabled, semaphore))
    
    await asyncio.gather(*tasks)
    logger.info("Scheduled run complete.")

def mark_token_invalid_pause(user_id: int):
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            user.auto_claim_enabled = 0
            session.commit()
            logger.info(f"Auto-claim paused due to invalid token for user {user_id}")
    except Exception as e:
        logger.error(f"DB error mark_token_invalid_pause {user_id}: {e}")
    finally:
        session.close()

async def process_user_today(application: Application, user_id, token, semaphore):
    async with semaphore:
        try:
            logger.info(f"Generating today recommendation for user {user_id}")
            result = await asyncio.wait_for(get_today_recommendation(token), timeout=40)
            # Create Telegraph page
            page_url = None
            try:
                page_url = await telegraph_service.create_page(
                    title=f"今日推荐",
                    content_nodes=build_telegraph_nodes_from_text(result, title=f"今日推荐")
                )
            except Exception as e:
                logger.error(f"Telegraph page error (today) for {user_id}: {e}")
            # Compose summary
            summary = sanitize_text(result)[:300] + ("..." if len(result) > 300 else "")
            if page_url:
                msg = f"📄 今日推荐（图文版）：{page_url}\n\n{summary}"
                await application.bot.send_message(chat_id=user_id, text=msg, disable_web_page_preview=True)
            else:
                await send_chunked_direct(application.bot, user_id, sanitize_text(result))
        except asyncio.TimeoutError:
            await application.bot.send_message(chat_id=user_id, text="⏰ 今日推荐生成超时，稍后再试。")
        except Exception as e:
            logger.error(f"Failed to generate today recommendation for user {user_id}: {e}")

async def scheduled_today_job(application: Application):
    logger.info("Running scheduled daily today-recommendation for all users...")
    users = get_all_users()
    semaphore = asyncio.Semaphore(4)
    tasks = []
    for user_id, token, _ in users:
        if token:
            tasks.append(process_user_today(application, user_id, token, semaphore))
    await asyncio.gather(*tasks)
    logger.info("Scheduled today recommendation complete.")

async def post_init(application: Application) -> None:
    """
    Set up bot commands menu on startup.
    """
    commands = [
        ("menu", "打开按钮菜单"),
        ("claim", "立即领券"),
        ("token", "设置 MCP Token"),
        ("account", "多账号管理"),
        ("calendar", "活动日历查询"),
        ("today", "今日智能推荐"),
        ("coupons", "查看可领优惠券"),
        ("mycoupons", "我的券包"),
        ("autoclaim", "自动领券设置"),
        ("autoclaimreport", "自动领券汇报设置"),
        ("stats", "领券统计"),
        ("status", "查看状态"),
        ("cleartoken", "清除 Token (解绑)"),
        ("help", "查看帮助")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set.")

def run_scheduler(application, loop):
    """
    Runs the schedule in a separate thread.
    """
    logger.info("Scheduler thread started")
    
    def job_wrapper_claim():
        asyncio.run_coroutine_threadsafe(scheduled_job(application), loop)
    def job_wrapper_today():
        asyncio.run_coroutine_threadsafe(scheduled_today_job(application), loop)

    # Schedule daily tasks
    schedule.every().day.at("10:30").do(job_wrapper_claim)
    schedule.every().day.at("10:35").do(job_wrapper_today)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# Keep-alive web server for PaaS (Koyeb/Render/HF Spaces)
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    timezone = os.environ.get("TZ", "Unknown (System Default)")
    
    # Check DB Status for logging
    db_status = "Connected"
    try:
        # Simple connection check
        with engine.connect() as conn:
            pass
    except Exception as e:
        db_status = f"Error: {str(e)}"

    print(f"\n🚀 Starting Flask server on port {port}...")
    print(f"🌍 Current Timezone: {timezone}")
    print(f"💾 Database Status: {db_status}")
    
    # Disable standard Flask logs to avoid clutter
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
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

    application = Application.builder().token(token).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("token", token_command))
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
    application.add_handler(CommandHandler("autoclaimreport", autoclaimreport_command))
    application.add_handler(CommandHandler("cleartoken", cleartoken_command))
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
