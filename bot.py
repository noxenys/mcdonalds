import sys
import subprocess
import importlib
import os
import base64
import hashlib

# Runtime Dependency Self-Check (Hotfix)
def check_and_install_packages():
    required = {
        'schedule': 'schedule',
        'flask': 'flask',
        'telegram': 'python-telegram-bot',
        'tenacity': 'tenacity',
        'sqlalchemy': 'sqlalchemy',
        'dotenv': 'python-dotenv',
        'mcp': 'mcp'
    }
    # Default to auto-installing dependencies to be more user-friendly on PaaS
    auto_install = os.getenv("AUTO_INSTALL_DEPS", "1").strip().lower() in {"1", "true", "yes"}
    missing_packages = []
    for module, package in required.items():
        try:
            importlib.import_module(module)
        except ImportError:
            if not auto_install:
                missing_packages.append(package)
                continue
            print(f"⚠️  Missing runtime dependency: {module}. Auto-installing {package}...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                importlib.import_module(module)
                print(f"✅  Installed {package}.")
            except Exception as e:
                print(f"❌  Failed to install {package}: {e}")
                missing_packages.append(package)
    return sorted(set(missing_packages))

missing_packages = check_and_install_packages()
if missing_packages:
    print(f"Error: Missing runtime dependencies: {', '.join(missing_packages)}")
    print("Install requirements first: pip install -r requirements.txt")
    sys.exit(1)

import logging
import re
import asyncio
import time
import threading
import schedule
from datetime import datetime, timedelta, timezone
from flask import Flask
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import RetryAfter
from claim_coupons import claim_for_token, list_available_coupons, list_my_coupons, list_campaign_calendar, get_today_recommendation, is_mcp_error_message, reorder_calendar_sections
from coupon_utils import get_cst_now, clean_markdown_text

async def send_chunked(update: Update, text: str, parse_mode=None, chunk_size: int = 3500):
    if not text:
        return
    kwargs = {"disable_web_page_preview": True}
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
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
            await safe_reply_text(update, p, **kwargs)
        except Exception:
            if parse_mode:
                try:
                    await safe_reply_text(update, p, disable_web_page_preview=True)
                except Exception:
                    await safe_reply_text(update, "[消息发送失败片段已省略]", disable_web_page_preview=True)
            else:
                await safe_reply_text(update, "[消息发送失败片段已省略]", disable_web_page_preview=True)

async def send_chunked_update(application, chat_id, text: str, chunk_size: int = 3500):
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
            await safe_bot_send_message(application.bot, chat_id, p, disable_web_page_preview=True)
        except Exception:
            await safe_bot_send_message(application.bot, chat_id, "[消息发送失败片段已省略]", disable_web_page_preview=True)

from telegraph_service import TelegraphService

IMG_URL_RE = re.compile(r"<img[^>]*src=\"([^\"]+)\"", re.IGNORECASE)

def clean_markdown(text):
    return clean_markdown_text(text)

def build_telegraph_nodes_from_text(text: str, title: str) -> list:
    if not text:
        return [{"tag": "p", "children": ["暂无内容"]}]
    nodes = []
    nodes.append({"tag": "h3", "children": [clean_markdown(title)]})
    
    # Process line by line to preserve order
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Extract images from this line
        imgs = IMG_URL_RE.findall(line)
        
        # Clean text by removing image tags
        text_content = re.sub(r"<img[^>]+>", "", line)
        cleaned_text = clean_markdown(text_content)
        
        # If there is text, add it first
        if cleaned_text and not cleaned_text.startswith("http"):
             # Remove markdown headers
            cleaned_text = re.sub(r"^#+\s*", "", cleaned_text)
            if cleaned_text:
                nodes.append({"tag": "p", "children": [cleaned_text]})
        
        # Then add images found in this line
        if imgs:
            for url in imgs:
                nodes.append({"tag": "figure", "children": [{"tag": "img", "attrs": {"src": url}}]})
                
    # footer
    nodes.append({"tag": "hr"})
    nodes.append({"tag": "p", "children": ["Generated by McdBot"]})
    return nodes

def build_today_telegraph_nodes(text: str, calendar_raw) -> list:
    nodes = []
    nodes.append({"tag": "h3", "children": ["今日推荐"]})
    
    # Process summary text (lines interspersed with images if any)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Extract images from this line
        imgs = IMG_URL_RE.findall(line)
        
        # Clean text by removing image tags
        text_content = re.sub(r"<img[^>]+>", "", line)
        cleaned_text = clean_markdown(text_content)
        
        # If there is text, add it first
        if cleaned_text and not cleaned_text.startswith("http"):
             # Remove markdown headers
            cleaned_text = re.sub(r"^#+\s*", "", cleaned_text)
            if cleaned_text:
                nodes.append({"tag": "p", "children": [cleaned_text]})
        
        # Then add images found in this line
        if imgs:
            for url in imgs:
                nodes.append({"tag": "figure", "children": [{"tag": "img", "attrs": {"src": url}}]})

    def _parse_date(value):
        if not value:
            return None
        if not isinstance(value, str):
            value = str(value)
        m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', value)
        if not m:
            return None
        year, month, day = m.groups()
        try:
            return datetime(int(year), int(month), int(day)).date()
        except ValueError:
            return None

    items = []
    if isinstance(calendar_raw, list):
        items = [item for item in calendar_raw if isinstance(item, dict)]
    elif isinstance(calendar_raw, dict):
        candidates = calendar_raw.get("items") or calendar_raw.get("campaigns") or calendar_raw.get("data") or calendar_raw.get("list")
        if isinstance(candidates, list):
            items = [item for item in candidates if isinstance(item, dict)]

    items = TelegraphService.sort_calendar_items(items) if items else []
    if items:
        today_date = get_cst_now().date()
        filtered = []
        for item in items:
            start = _parse_date(item.get("start") or item.get("startDate") or item.get("date") or item.get("begin"))
            end = _parse_date(item.get("end") or item.get("endDate") or item.get("finish"))
            if start and end:
                if start <= today_date <= end:
                    filtered.append(item)
            elif start:
                if start == today_date:
                    filtered.append(item)
            elif end:
                if end == today_date:
                    filtered.append(item)
            else:
                filtered.append(item)
        items = filtered


    featured = []
    for item in items:
        image_url = item.get("image") or item.get("imageUrl") or item.get("img")
        if image_url:
            featured.append(item)
        if len(featured) >= 5: # Increase limit to show more activities
            break

    if featured:
        nodes.append({"tag": "hr"})
        nodes.append({"tag": "h4", "children": ["📅 精选活动详情"]})

    for item in featured:
        title = clean_markdown(item.get("title") or item.get("name") or "精选活动")
        image_url = item.get("image") or item.get("imageUrl") or item.get("img")
        
        nodes.append({"tag": "hr"})
        nodes.append({"tag": "h3", "children": [title]})
        
        # Text first
        content = item.get("content") or item.get("desc")
        if content:
            nodes.append({"tag": "p", "children": [{"tag": "b", "children": ["活动详情:"]}]})
            for line in str(content).splitlines():
                l = clean_markdown(line)
                if not l:
                    continue
                nodes.append({"tag": "p", "children": [l]})
        
        # Then Image
        if image_url:
            nodes.append({"tag": "figure", "children": [
                {"tag": "img", "attrs": {"src": image_url}},
                {"tag": "figcaption", "children": ["活动海报"]}
            ]})

    nodes.append({"tag": "hr"})
    nodes.append({"tag": "p", "children": ["Generated by McdBot"]})
    return nodes

# Initialize Telegraph Service
telegraph_service = TelegraphService()

# SQLAlchemy imports
from sqlalchemy import create_engine, Column, Integer, String, DateTime, func, text, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

# Load environment variables
load_dotenv()

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def _get_token_secret_bytes():
    secret = os.getenv("MCD_TOKEN_SECRET", "").strip()
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).digest()

def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

def _encode_token(token: str) -> str:
    if not token:
        return token
    key = _get_token_secret_bytes()
    if not key:
        return token
    try:
        raw = token.encode("utf-8")
        enc = _xor_bytes(raw, key)
        return "enc:" + base64.urlsafe_b64encode(enc).decode("ascii")
    except Exception as e:
        logger.error(f"Failed to encode token: {e}")
        return token

def _decode_token(token: str) -> str:
    if not token:
        return token
    if not isinstance(token, str):
        token = str(token)
    if not token.startswith("enc:"):
        return token
    key = _get_token_secret_bytes()
    if not key:
        logger.error("Encrypted token detected but MCD_TOKEN_SECRET is not set.")
        return None
    try:
        data = base64.urlsafe_b64decode(token[4:])
        raw = _xor_bytes(data, key)
        return raw.decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to decode token: {e}")
        return None

async def safe_reply_text(update: Update, text: str, **kwargs):
    for _ in range(3):
        try:
            return await update.message.reply_text(text, **kwargs)
        except RetryAfter as e:
            delay = int(getattr(e, "retry_after", 1) or 1)
            await asyncio.sleep(delay)
        except Exception:
            raise

async def safe_bot_send_message(bot, chat_id, text: str, **kwargs):
    for _ in range(3):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except RetryAfter as e:
            delay = int(getattr(e, "retry_after", 1) or 1)
            await asyncio.sleep(delay)
        except Exception:
            raise

# ==================== Message Formatting Constants ====================

# Emoji definitions for consistency
EMOJI_SUCCESS = "✅"
EMOJI_ERROR = "❌"
EMOJI_WARNING = "⚠️"
EMOJI_INFO = "ℹ️"
EMOJI_ENABLED = "✅"
EMOJI_DISABLED = "🚫"
EMOJI_STATS = "📊"
EMOJI_USER = "👤"
EMOJI_ID = "🆔"
EMOJI_CALENDAR = "📅"
EMOJI_SETTINGS = "⚙️"
EMOJI_RECORD = "📝"
EMOJI_HINT = "💡"

# Visual separator
SEPARATOR = "━━━━━━━━━━━━━━━━━━━"

# Common help text
COMMAND_HELP_TEXT = """常用命令：
/claim - 立即领券
/coupons - 查看当前可领优惠券
/mycoupons - 查看你已拥有的优惠券
/calendar - 查看活动日历
/today - 今日智能用券建议
/status - 查看当前状态
/stats - 查看领券统计
/autoclaim on/off - 开启或关闭每日自动领券
/account add/use/list/del - 多账号管理
/unbind - 解除绑定
/admin - 管理员总览"""

# Message formatting helper functions
def format_error_msg(message: str, show_help: bool = False) -> str:
    """Format error message with consistent style."""
    msg = f"{EMOJI_ERROR} {message}"
    if show_help:
        msg += f"\n\n{EMOJI_HINT} 需要帮助？发送 /help 查看使用说明"
    return msg

def format_success_msg(message: str, extra: str = "") -> str:
    """Format success message with consistent style."""
    msg = f"{EMOJI_SUCCESS} {message}"
    if extra:
        msg += f"\n\n{extra}"
    return msg

def format_warning_msg(message: str) -> str:
    """Format warning message with consistent style."""
    return f"{EMOJI_WARNING} {message}"

def format_info_msg(message: str) -> str:
    """Format info message with consistent style."""
    return f"{EMOJI_INFO} {message}"

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback to SQLite
    default_db_path = os.path.join("data", "users.db")
    DB_FILE = os.getenv("DB_PATH", default_db_path)
    db_dir = os.path.dirname(DB_FILE)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
            logger.info(f"Created database directory: {db_dir}")
        except OSError as e:
            logger.error(f"Failed to create database directory {db_dir}: {e}")
    DATABASE_URL = f"sqlite:///{DB_FILE}"

logger.info(f"Using Database: {DATABASE_URL.split('://')[0]}://...")

# SQLAlchemy Setup
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    user_id = Column(BigInteger, primary_key=True)
    username = Column(String)
    mcp_token = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    auto_claim_enabled = Column(Integer, default=1)
    claim_report_enabled = Column(Integer, default=1)
    last_claim_at = Column(DateTime)
    last_claim_success = Column(Integer)
    total_success = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)

class Account(Base):
    __tablename__ = 'accounts'
    # Composite primary key manually handled or use id
    # Original schema: PRIMARY KEY (user_id, name)
    user_id = Column(BigInteger, primary_key=True)
    name = Column(String, primary_key=True)
    mcp_token = Column(String)
    is_active = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

engine = create_engine(DATABASE_URL)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified/created.")
        
        # Check for schema updates (columns added later)
        # This is a basic migration check for existing SQLite users migrating to newer version
        # For Postgres, create_all handles creation, but ALTERS need manual handling or migration tools.
        # Here we just try to add columns if they might be missing in old SQLite files.
        # For a proper production app, use Alembic.
        if "sqlite" in DATABASE_URL:
            with engine.connect() as conn:
                alter_statements = [
                    "ALTER TABLE users ADD COLUMN auto_claim_enabled INTEGER DEFAULT 1",
                    "ALTER TABLE users ADD COLUMN claim_report_enabled INTEGER DEFAULT 1",
                    "ALTER TABLE users ADD COLUMN last_claim_at TIMESTAMP",
                    "ALTER TABLE users ADD COLUMN last_claim_success INTEGER",
                    "ALTER TABLE users ADD COLUMN total_success INTEGER DEFAULT 0",
                    "ALTER TABLE users ADD COLUMN total_failed INTEGER DEFAULT 0"
                ]
                for stmt in alter_statements:
                    try:
                        conn.execute(text(stmt))
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

# Helper to get session
def get_db():
    return SessionLocal()

# --- Database Access Layer (Refactored to use SQLAlchemy) ---

def get_active_account(user_id):
    session = get_db()
    try:
        account = session.query(Account).filter(Account.user_id == user_id, Account.is_active == 1).first()
        if account:
            return (account.name, _decode_token(account.mcp_token))
        return None
    finally:
        session.close()

def get_accounts(user_id):
    session = get_db()
    try:
        accounts = session.query(Account).filter(Account.user_id == user_id).all()
        return [(acc.name, _decode_token(acc.mcp_token), acc.is_active) for acc in accounts]
    finally:
        session.close()

def upsert_account(user_id, name, token, set_active):
    session = get_db()
    try:
        account = session.query(Account).filter(Account.user_id == user_id, Account.name == name).first()
        stored_token = _encode_token(token)
        if account:
            account.mcp_token = stored_token
            if set_active:
                # Deactivate others
                session.query(Account).filter(Account.user_id == user_id).update({"is_active": 0})
                account.is_active = 1
        else:
            if set_active:
                session.query(Account).filter(Account.user_id == user_id).update({"is_active": 0})
            account = Account(user_id=user_id, name=name, mcp_token=stored_token, is_active=1 if set_active else 0)
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
        return _decode_token(user.mcp_token) if user else None
    finally:
        session.close()

def save_user_token(user_id, username, token, sync_default_account=True):
    session = get_db()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        stored_token = _encode_token(token)
        if user:
            user.username = username
            user.mcp_token = stored_token
            # Ensure auto_claim is enabled if it was null (though default handles it)
        else:
            user = User(user_id=user_id, username=username, mcp_token=stored_token, auto_claim_enabled=1)
            session.add(user)
        session.commit()
        
        # Keep compatibility with legacy single-account flow.
        if sync_default_account:
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
        return [(u.user_id, _decode_token(u.mcp_token), u.claim_report_enabled) for u in users]
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
            user.last_claim_at = get_cst_now()
            user.last_claim_success = 1 if success else 0
            user.total_success = (user.total_success or 0) + (1 if success else 0)
            user.total_failed = (user.total_failed or 0) + (0 if success else 1)
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Error in update_claim_stats: {e}")
    finally:
        session.close()

def _coerce_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None

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
        "👋 欢迎使用麦当劳自动领券 Bot！\n"
        f"{SEPARATOR}\n\n"
        "🔑 请先发送你的 MCP Token 给我完成绑定\n"
        "🔗 获取地址：https://open.mcd.cn/mcp/console\n\n"
        f"{SEPARATOR}\n\n"
        "📱 你可以直接使用底部菜单按钮，也可以使用以下命令：\n\n"
        f"{COMMAND_HELP_TEXT}",
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
        await update.message.reply_text(format_error_msg("Token 看起来太短了，请检查是否正确"))
        return

    await update.message.reply_text("🔍 正在验证你的 Token，请稍等...")
    
    # Reuse verification logic
    result = await claim_for_token(token, enable_push=False)
    
    if is_result_error_message(result):
         await update.message.reply_text(f"❌ Token 无效或连接失败。\n{result}")
    else:
        save_user_token(user_id, username, token)
        await update.message.reply_text(
            format_success_msg(
                "Token 验证成功并已保存！",
                f"{SEPARATOR}\n\n{result}\n\n{SEPARATOR}\n\n⏰ 之后我会在每天 10:30 自动为你领券"
            )
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 使用说明\n"
        f"{SEPARATOR}\n\n"
        "1. 先在 https://open.mcd.cn/mcp/console 获取你的 MCP Token\n"
        "2. 将 Token 直接发送给我完成绑定\n"
        "3. 绑定后，我会在每天 10:30 自动帮你领券\n\n"
        f"{SEPARATOR}\n\n"
        f"{COMMAND_HELP_TEXT}"
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
        progress_msg = await update.message.reply_text("🔍 正在验证你的 Token，请稍等...")
        
        try:
            result = await claim_for_token(text, enable_push=False)
            
            if is_result_error_message(result):
                 await update.message.reply_text(format_error_msg(f"Token 无效或连接失败\n{result}", show_help=True))
            else:
                save_user_token(user_id, username, text)
                await update.message.reply_text(
                    f"✅ Token 验证成功并已保存！\n\n"
                    f"我已经帮你执行了一次领券：\n{result}\n\n"
                    f"之后我会在每天 10:30 自动为你领券。"
                )
        finally:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
    else:
        await update.message.reply_text("❓ 没看懂，你可以直接把 MCP Token 发给我完成绑定。")

async def claim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    progress_msg = await update.message.reply_text("🍟 正在为你领券...")
    try:
        result = await claim_for_token(token, enable_push=False)
        success = is_claim_success_result(result)
        update_claim_stats(user_id, success)
        display_result = sanitize_text(result or "")
        if display_result:
            await send_chunked(update, f"完成！\n{display_result}", parse_mode=None)
        else:
            await update.message.reply_text("完成！")
    finally:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    args = context.args
    date = args[0] if args else None
    
    # If no date argument, assume today in CST
    if not date:
        cst_now = get_cst_now()
        date = cst_now.strftime("%Y-%m-%d")

    progress_msg = await update.message.reply_text("🗓️ 正在为你查询活动日历，请稍等...")

    try:
        raw_result = await list_campaign_calendar(token, date, return_raw=True)

        calendar_nodes = None
        text_result = None
        summary_hint = ""

        # 优先使用结构化 JSON 构建 Telegraph 图文卡片
        if isinstance(raw_result, list):
            items = [item for item in raw_result if isinstance(item, dict)]
            if items:
                items = TelegraphService.sort_calendar_items(items)
                calendar_nodes = telegraph_service.format_calendar_to_nodes(items)
                titles = []
                for item in items:
                    t = item.get("title") or item.get("name")
                    if t:
                        titles.append(t)
                if titles:
                    summary_hint = "活动列表：" + " ｜ ".join(titles[:6])
        elif isinstance(raw_result, dict):
            candidates = raw_result.get("items") or raw_result.get("campaigns") or raw_result.get("data") or raw_result.get("list")
            if isinstance(candidates, list):
                items = [item for item in candidates if isinstance(item, dict)]
                if items:
                    items = TelegraphService.sort_calendar_items(items)
                    calendar_nodes = telegraph_service.format_calendar_to_nodes(items)
                    titles = []
                    for item in items:
                        t = item.get("title") or item.get("name")
                        if t:
                            titles.append(t)
                    if titles:
                        summary_hint = "活动列表：" + " ｜ ".join(titles[:6])
            else:
                text_result = str(raw_result)
        else:
            text_result = raw_result

        if isinstance(text_result, str):
            text_result = strip_mcp_header(text_result)
            text_result = reorder_calendar_sections(text_result)

        if isinstance(text_result, str) and is_mcp_error_message(text_result):
            await update.message.reply_text("今天麦当劳 MCP 服务似乎出问题了，我暂时查不到活动日历，可以稍后再试一次 /calendar。")
            return

        if not calendar_nodes and not text_result:
            await update.message.reply_text("暂未查询到活动信息。")
        else:
            if not text_result:
                text_result = summary_hint or "麦当劳活动日历"

            sanitized = sanitize_text(text_result)
            page_url = None
            try:
                page_url = await telegraph_service.create_page(
                    title="麦当劳活动日历",
                    content_nodes=calendar_nodes or build_telegraph_nodes_from_text(text_result, title="麦当劳活动日历")
                )
            except Exception as e:
                logger.error(f"Telegraph page error: {e}")
            if page_url:
                summary = sanitized[:300] + ("..." if len(sanitized) > 300 else "")
                msg = f"📄 活动日历（图文版）：{page_url}\n\n{summary}"
                await safe_reply_text(update, msg)
            else:
                await send_chunked(update, sanitized, parse_mode=None)

    finally:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

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
        # 去掉多余的反斜杠和Markdown粗体
        l = l.replace("\\", "").replace("**", "")
        cleaned_lines.append(l)
    cleaned = "\n".join(cleaned_lines)
    # 避免 Markdown 特殊字符影响，统一发送纯文本（不设置 parse_mode）
    # 但仍可简单规范标题符号
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    return cleaned

def strip_mcp_header(text: str) -> str:
    if not text:
        return ""
    raw_lines = text.splitlines()
    cleaned_raw = []
    skipping_header = True
    for line in raw_lines:
        stripped = line.strip()
        if skipping_header:
            if not stripped:
                continue
            if "Client 支持 Markdown 渲染" in stripped:
                continue
            if stripped.startswith("### 当前时间") or stripped.startswith("当前时间：") or stripped.startswith("当前时间:"):
                continue
            skipping_header = False
        cleaned_raw.append(line)
    return "\n".join(cleaned_raw)

def is_result_error_message(result: str) -> bool:
    if result is None:
        return True
    text = str(result).strip()
    if not text:
        return True
    if is_mcp_error_message(text):
        return True

    lower = text.lower()
    if "mcp" in lower and ("429" in lower or "异常" in text or "error" in lower):
        return True
    if any(marker in lower for marker in [
        "unauthorized",
        "invalid token",
        "token invalid",
        "forbidden",
        "401",
    ]):
        return True
    if any(marker in text for marker in [
        "Token 无效",
        "token 无效",
        "Token已失效",
        "token已失效",
        "未授权",
        "认证失败",
    ]):
        return True
    if re.search(r"(^|\n)\s*(?:❌|错误[:：]?|error[:：]?)", text, re.IGNORECASE):
        return True

    fail_match = (
        re.search(r"失败\s*[:：]\s*(\d+)", text) or
        re.search(r"\bfail(?:ed|ure)?\b\s*[:：]?\s*(\d+)", lower)
    )
    success_match = (
        re.search(r"成功\s*[:：]\s*(\d+)", text) or
        re.search(r"\bsuccess\b\s*[:：]?\s*(\d+)", lower)
    )
    if fail_match and int(fail_match.group(1)) > 0:
        success_count = int(success_match.group(1)) if success_match else 0
        if success_count == 0:
            return True
    return False

def is_token_invalid_result(result: str) -> bool:
    if not result:
        return False
    text = str(result)
    lower = text.lower()
    if any(marker in lower for marker in ["401", "unauthorized", "invalid token", "token invalid", "forbidden"]):
        return True
    return any(marker in text for marker in ["Token 无效", "token 无效", "Token已失效", "token已失效", "未授权"])

def is_claim_success_result(result: str) -> bool:
    if is_result_error_message(result):
        return False
    text = str(result)
    lower = text.lower()
    success_match = (
        re.search(r"成功\s*[:：]\s*(\d+)", text) or
        re.search(r"\bsuccess\b\s*[:：]?\s*(\d+)", lower)
    )
    fail_match = (
        re.search(r"失败\s*[:：]\s*(\d+)", text) or
        re.search(r"\bfail(?:ed|ure)?\b\s*[:：]?\s*(\d+)", lower)
    )
    if success_match or fail_match:
        success_count = int(success_match.group(1)) if success_match else 0
        fail_count = int(fail_match.group(1)) if fail_match else 0
        if success_count == 0 and fail_count > 0:
            return False
        if success_count > 0:
            return True
    return True

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return
    progress_msg = await update.message.reply_text("🤖 正在结合活动日历和可领优惠券为你生成今天的用券建议，请稍等...")
    try:
        result = await asyncio.wait_for(get_today_recommendation(token), timeout=40)
        
        # 检查结果是否为空或错误
        if is_result_error_message(result):
            if progress_msg:
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                progress_msg = None
            await update.message.reply_text("今天麦当劳 MCP 服务似乎挂了，我暂时没法生成今日推荐，可以稍后再试一次 /today。")
            return
        
        # Calculate today in CST (UTC+8)
        cst_now = get_cst_now()
        today_str = cst_now.strftime("%Y-%m-%d")

        raw_calendar = await list_campaign_calendar(token, date=today_str, return_raw=True)
        sanitized = sanitize_text(result)
        
        # 再次检查sanitized结果是否为空
        if not sanitized or len(sanitized.strip()) < 10:
            if progress_msg:
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                progress_msg = None
            await update.message.reply_text("⚠️ 今日推荐内容为空，可能是服务异常，请稍后再试。")
            return
        
        page_url = None
        try:
            page_url = await telegraph_service.create_page(
                title="今日推荐",
                content_nodes=build_today_telegraph_nodes(result, raw_calendar)
            )
        except Exception as e:
            logger.error(f"Telegraph page error: {e}")
        
        if page_url:
            summary = sanitized[:300] + ("..." if len(sanitized) > 300 else "")
            msg = f"📄 今日推荐（图文版）：{page_url}\n\n{summary}"
            await safe_reply_text(update, msg)
        else:
            await send_chunked(update, sanitized, parse_mode=None)
    except asyncio.TimeoutError:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            progress_msg = None
        await update.message.reply_text(
            "⏰ 今日推荐生成超时，可能是麦当劳 MCP 服务响应过慢。\n"
            "你可以先使用 /coupons 和 /calendar 单独查看，稍后再试 /today。"
        )
    except Exception as e:
        logger.error(f"Today command failed for user {user_id}: {e}", exc_info=True)
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            progress_msg = None
        await update.message.reply_text(
            f"❌ 生成今日推荐时出现错误，请稍后再试。\n\n"
            f"💡 提示：你可以先使用 /coupons 和 /calendar 单独查看。\n\n"
            f"错误详情：{str(e)[:100]}"
        )
    finally:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

async def coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    progress_msg = await update.message.reply_text("📋 正在为你查询当前可领优惠券，请稍等...")
    try:
        result = await list_available_coupons(token)
        await update.message.reply_text(result or "暂无可领优惠券。")
    finally:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

async def my_coupons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)

    if not token:
        await update.message.reply_text("⚠️ 你还没有绑定 MCP Token，请先把 Token 发给我。")
        return

    progress_msg = await update.message.reply_text("🎟️ 正在为你查询你已拥有的优惠券，请稍等...")
    try:
        result = await list_my_coupons(token)
        await update.message.reply_text(result or "暂未查询到你的优惠券。")
    finally:
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    delete_user_token(user_id)
    await update.message.reply_text("🗑️ 已删除你的 Token，我将不再自动为你领券。")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    row = get_user_stats_and_status(user_id)

    if not token or not row:
        await update.message.reply_text(format_warning_msg("你还没有绑定 MCP Token，请先把 Token 发给我"))
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
        f"{EMOJI_STATS} 当前账号状态\n"
        f"{SEPARATOR}\n\n"
        f"{EMOJI_USER} 用户：@{username or '未知'}\n"
        f"{EMOJI_ID} ID：{user_id}\n\n"
        f"{SEPARATOR}\n\n"
        f"{EMOJI_SETTINGS} 功能设置\n"
        f"自动领券：{EMOJI_ENABLED + ' 已开启' if auto_enabled else EMOJI_DISABLED + ' 已关闭'}\n"
        f"领券汇报：{EMOJI_ENABLED + ' 已开启' if report_enabled else EMOJI_DISABLED + ' 已关闭'}\n\n"
        f"{SEPARATOR}\n\n"
        f"{EMOJI_RECORD} 领券记录\n"
        f"上次时间：{last_claim_at or '暂无记录'}\n"
        f"上次结果：{last_result_text}\n\n"
        f"{EMOJI_HINT} 提示：Token失效时系统会自动关闭自动领券\n"
        "   更新Token后使用 /autoclaim on 重新开启"
    )

    await update.message.reply_text(msg)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    token = get_user_token(user_id)
    row = get_user_stats_and_status(user_id)

    if not token or not row:
        await update.message.reply_text(format_warning_msg("暂无数据，你还没有绑定 MCP Token 或从未领过券"))
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
        "📈 你的领券统计\n"
        f"{SEPARATOR}\n\n"
        f"当前称号：{title}\n"
        f"总尝试次数：{total}\n"
        f"成功次数：{success_count}\n"
        f"失败次数：{failed_count}{luck_status}"
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
    args = context.args
    if not args:
        msg = (
            "👤 多账号管理\n\n"
            "你可以同时绑定多个麦当劳账号，并随时切换。\n\n"
            "📋 命令列表：\n"
            "/account add <名称> <Token> - 添加新账号\n"
            "/account use <名称> - 切换到指定账号\n"
            "/account list - 查看已添加的账号\n"
            "/account del <名称> - 删除指定账号\n"
        )
        await update.message.reply_text(msg)
        return
    sub = args[0].lower()
    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("❌ 格式错误\n请使用：/account add <名称> <Token>")
            return
        name = args[1]
        new_token = " ".join(args[2:])
        if len(new_token) < 20:
             await update.message.reply_text("❌ Token 无效或太短，请检查。")
             return
        
        # Verify token validity before adding
        await update.message.reply_text(f"🔍 正在验证账号 {name} 的 Token...")
        result = await claim_for_token(new_token, enable_push=False)
        
        if is_result_error_message(result):
             await update.message.reply_text(f"❌ Token 验证失败，账号未添加。\n错误信息：{result}")
             return

        upsert_account(user_id, name, new_token, True)
        save_user_token(user_id, update.effective_user.username, new_token, sync_default_account=False)
        await update.message.reply_text(f"✅ 账号 {name} 添加成功并设为当前账号！")
        
    elif sub == "use":
        if len(args) < 2:
            await update.message.reply_text("❌ 格式错误\n请使用：/account use <名称>")
            return
        name = args[1]
        accounts = get_accounts(user_id)
        target = None
        for acc in accounts:
            if acc[0] == name:
                target = acc
                break
        if not target:
            await update.message.reply_text(f"❌ 未找到名为 {name} 的账号。")
            return
        if not target[1]:
            await update.message.reply_text("⚠️ 该账号的 Token 无法读取（可能启用了 MCD_TOKEN_SECRET 但当前未设置）。请先配置正确的密钥。")
            return
        set_active_account(user_id, name)
        save_user_token(user_id, update.effective_user.username, target[1], sync_default_account=False)
        await update.message.reply_text(f"✅ 已切换到账号 {name}。")
        
    elif sub == "list":
        accounts = get_accounts(user_id)
        if not accounts:
            await update.message.reply_text("⚠️ 你还没有添加任何账号。")
            return
        lines = []
        for name, acc_token, is_active in accounts:
            mark = "✅" if is_active else "⚪️"
            lines.append(f"{mark} {name}")
        await update.message.reply_text("📋 你的账号列表：\n\n" + "\n".join(lines))
        
    elif sub == "del":
        if len(args) < 2:
            await update.message.reply_text("❌ 格式错误\n请使用：/account del <名称>")
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
            await update.message.reply_text(f"❌ 未找到名为 {name} 的账号。")
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
                save_user_token(user_id, update.effective_user.username, first_token, sync_default_account=False)
                await update.message.reply_text(f"✅ 已删除账号 {name}。\n自动切换到 {first_name}。")
            else:
                delete_user_token(user_id)
                await update.message.reply_text(f"✅ 已删除账号 {name}。\n你当前没有绑定任何账号。")
        else:
            await update.message.reply_text(f"✅ 已删除账号 {name}。")
            
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
                await safe_bot_send_message(context.bot, uid, f"📢 管理员通知：\n\n{message}")
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
            try:
                row = get_user_stats_and_status(user_id)
                if row:
                    last_claim_at = row[3]
                    last_claim_success = row[4]
                    if last_claim_success == 1:
                        last_date = _coerce_date(last_claim_at)
                        if last_date == get_cst_now().date():
                            logger.info(f"Skipping auto-claim for user {user_id}: already claimed today.")
                            return
            except Exception as e:
                logger.warning(f"Failed to check last claim for user {user_id}: {e}")

            logger.info(f"Claiming for user {user_id}")
            result = await claim_for_token(token, enable_push=False)
            success = is_claim_success_result(result)
            token_invalid = is_token_invalid_result(result)
            update_claim_stats(user_id, success)
            if token_invalid:
                set_auto_claim_enabled(user_id, False)

            # Only send message if report_enabled is True (default 1) or None (treated as True)
            if report_enabled is None or report_enabled == 1:
                message = f"🔔 每日自动领券结果：\n\n{result}"

                if token_invalid:
                    message += "\n\n⚠️ 注意：你的 Token 可能已失效或无效，请重新发送新的 Token 进行绑定。"
                elif is_result_error_message(result):
                    message += "\n\n⚠️ 本次领券失败，可能是服务短暂异常，建议稍后手动重试。"
                elif success:
                    # Add random quote for successful claims
                    quote = random.choice(MCD_QUOTES)
                    message += f"\n\n🍟 {quote}"

                await safe_bot_send_message(application.bot, user_id, message)
        except Exception as e:
            logger.error(f"Failed to auto-claim for user {user_id}: {e}")

async def scheduled_job(application: Application):
    logger.info("Running scheduled daily claim for all users...")
    users = get_all_users()
    
    # Limit concurrency to 5 to avoid overwhelming resources
    semaphore = asyncio.Semaphore(5)
    tasks = []
    
    for user_id, token, report_enabled in users:
        if not token:
            continue
        tasks.append(process_user_claim(application, user_id, token, report_enabled, semaphore))
    
    await asyncio.gather(*tasks)
    logger.info("Scheduled run complete.")

async def process_user_today(application: Application, user_id, token, semaphore):
    async with semaphore:
        try:
            logger.info(f"Generating today recommendation for user {user_id}")
            result = await asyncio.wait_for(get_today_recommendation(token), timeout=40)
            
            # 检查结果是否为空或错误
            if is_result_error_message(result):
                await safe_bot_send_message(application.bot, user_id, "今天麦当劳 MCP 服务似乎挂了，我暂时没法生成今日推荐，可以稍后再试一次。")
                return
            
            raw_calendar = await list_campaign_calendar(token, return_raw=True)
            sanitized = sanitize_text(result)
            
            # 检查sanitized结果
            if not sanitized or len(sanitized.strip()) < 10:
                await safe_bot_send_message(application.bot, user_id, "⚠️ 今日推荐内容为空，可能是服务异常，请稍后再试。")
                return
            
            page_url = None
            try:
                page_url = await telegraph_service.create_page(
                    title="今日推荐",
                    content_nodes=build_today_telegraph_nodes(result, raw_calendar)
                )
            except Exception as e:
                logger.error(f"Telegraph page error (today) for {user_id}: {e}")
            
            if page_url:
                summary = sanitized[:300] + ("..." if len(sanitized) > 300 else "")
                msg = f"📄 今日推荐（图文版）：{page_url}\n\n{summary}"
                await safe_bot_send_message(application.bot, user_id, msg)
            else:
                await send_chunked_update(application, user_id, sanitized)
        except asyncio.TimeoutError:
            await safe_bot_send_message(application.bot, user_id, "⏰ 今日推荐生成超时，稍后再试。")
        except Exception as e:
            logger.error(f"Failed to generate today recommendation for user {user_id}: {e}", exc_info=True)
            await safe_bot_send_message(application.bot, user_id, "❌ 生成今日推荐时出现错误，请稍后再试。")

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

# ==================== New Feature: Expiry Reminder ====================
from coupon_utils import check_expiring_soon, format_expiry_reminder

async def scheduled_expiry_check(application: Application):
    """检查所有用户的优惠券过期情况并发送提醒"""
    logger.info("Running scheduled expiry check...")
    users = get_all_users()
    
    for user_id, token, _ in users:
        if not token:
            continue
        
        try:
            # 获取用户的优惠券（获取原始数据，包含有效期信息）
            raw_coupons = await list_my_coupons(token, return_raw=True)
            if not raw_coupons:
                continue
            
            # 转换为文本格式
            if isinstance(raw_coupons, str):
                if is_result_error_message(raw_coupons):
                    continue
                coupons_text = raw_coupons
            else:
                coupons_text = ""
                for content in raw_coupons:
                    if content.type == "text":
                        coupons_text += content.text + "\n"
            
            if not coupons_text or is_mcp_error_message(coupons_text):
                continue
            
            # 检查即将过期的券（3天内）
            expiring = check_expiring_soon(coupons_text, days_threshold=3)
            
            if expiring:
                reminder_msg = format_expiry_reminder(expiring)
                await safe_bot_send_message(application.bot, user_id, reminder_msg)
                logger.info(f"Sent expiry reminder to user {user_id}, {len(expiring)} coupons expiring")
        
        except Exception as e:
            logger.error(f"Failed to check expiry for user {user_id}: {e}")
        
        # 避免请求过快
        await asyncio.sleep(0.5)
    
    logger.info("Scheduled expiry check complete.")

async def scheduled_meal_reminder(application: Application, meal_type: str):
    """
    用餐时间智能提醒（午餐或晚餐）
    
    Args:
        meal_type: "lunch" 或 "dinner"
    """
    logger.info(f"Running scheduled meal reminder ({meal_type})...")
    users = get_all_users()
    
    # 设置问候语
    if meal_type == "lunch":
        greeting = "🍔 午餐时间到！"
        time_hint = "中午"
    else:
        greeting = "🍗 晚餐时间到！"
        time_hint = "晚上"
    
    for user_id, token, _ in users:
        if not token:
            continue
        
        try:
            # 获取用户已领取的优惠券
            raw_coupons = await list_my_coupons(token, return_raw=True)
            if not raw_coupons:
                continue
            
            # 转换为文本格式
            if isinstance(raw_coupons, str):
                if is_result_error_message(raw_coupons):
                    continue
                coupons_text = raw_coupons
            else:
                coupons_text = ""
                for content in raw_coupons:
                    if content.type == "text":
                        coupons_text += content.text + "\n"
            
            if not coupons_text or is_mcp_error_message(coupons_text):
                continue
            
            # 解析优惠券（简单提取券名）
            available_coupons = []
            lines = coupons_text.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('##'):
                    # 提取券名
                    coupon_name = line.lstrip('#').strip()
                    if coupon_name and coupon_name != '您的优惠券列表':
                        available_coupons.append(coupon_name)
            
            # 只推送有券的用户
            if not available_coupons:
                continue
            
            # 限制显示数量
            show_count = min(len(available_coupons), 5)
            
            # 构建消息
            msg_parts = [
                greeting,
                SEPARATOR,
                "",
                f"你有 {len(available_coupons)} 张优惠券可用：",
                ""
            ]
            
            for i, coupon in enumerate(available_coupons[:show_count], 1):
                msg_parts.append(f"{i}. {coupon}")
            
            if len(available_coupons) > show_count:
                msg_parts.append(f"\n还有{len(available_coupons) - show_count}张券...")
            
            msg_parts.extend([
                "",
                f"💡 {time_hint}用券最划算，记得使用哦~",
                "",
                "发送 /mycoupons 查看详情"
            ])
            
            reminder_msg = "\n".join(msg_parts)
            await safe_bot_send_message(application.bot, user_id, reminder_msg)
            logger.info(f"Sent {meal_type} reminder to user {user_id}, {len(available_coupons)} coupons available")
        
        except Exception as e:
            logger.error(f"Failed to send {meal_type} reminder to user {user_id}: {e}")
        
        # 避免请求过快
        await asyncio.sleep(0.5)
    
    logger.info(f"Scheduled {meal_type} reminder complete.")

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
    def job_wrapper_expiry():
        asyncio.run_coroutine_threadsafe(scheduled_expiry_check(application), loop)
    def job_wrapper_lunch():
        asyncio.run_coroutine_threadsafe(scheduled_meal_reminder(application, "lunch"), loop)
    def job_wrapper_dinner():
        asyncio.run_coroutine_threadsafe(scheduled_meal_reminder(application, "dinner"), loop)

    # Schedule daily tasks (use CST time to avoid server timezone drift)
    schedule_map = {
        "10:30": job_wrapper_claim,   # 自动领券
        "10:35": job_wrapper_today,   # 今日推荐
        "11:30": job_wrapper_lunch,   # 午餐提醒
        "17:30": job_wrapper_dinner,  # 晚餐提醒
        "20:00": job_wrapper_expiry,  # 过期提醒
    }
    last_run = {}

    while True:
        cst_now = get_cst_now()
        hhmm = cst_now.strftime("%H:%M")
        if hhmm in schedule_map:
            last_date = last_run.get(hhmm)
            if last_date != cst_now.date():
                schedule_map[hhmm]()
                last_run[hhmm] = cst_now.date()
        time.sleep(30)

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
