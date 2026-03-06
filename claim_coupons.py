import asyncio
import os
import sys
import time
import re
import random
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from notify import push_all
from coupon_utils import get_cst_now, clean_markdown_text

load_dotenv()

MCP_SERVER_URL = "https://mcp.mcd.cn/mcp-servers/mcd-mcp"

def clean_text(text):
    """Clean markdown formatting from text"""
    return clean_markdown_text(text)

def cleanup_for_telegram(text):
    """
    Cleans up and formats the text for better Telegram display.
    Handles both coupon lists and claim results.
    """
    lines = []
    
    raw_lines = text.splitlines()

    # Strip tool级别的 Markdown 提示头
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
    raw_lines = cleaned_raw
    
    full_text = "\n".join(raw_lines)
    # Claim result may contain "失败: 0张", so detect claim output before generic error handling.
    claim_marker_re = re.compile(r"\bcoupon\s*(?:id|code)\b|couponid|couponcode|券码|券号|兑换码", re.IGNORECASE)
    is_claim_result = bool(claim_marker_re.search(full_text))
    img_re = re.compile(r"<img[^>]*src=[\"']([^\"']+)[\"']", re.IGNORECASE)
    summary_re = re.compile(r"\*\*[^*]+\*\*\s*[:\uFF1A]\s*\S+")
    
    if is_claim_result:
        # Format claim results: extract coupon name and code, hide technical details
        formatted_lines = []
        header_lines = []
        current_coupon = {}
        parsed_coupons = []
        in_coupon_section = False
        
        for line in raw_lines:
            stripped = line.strip()
            line_lower = line.lower()
            
            # Capture header/summary lines before coupons
            if not in_coupon_section and "couponid" not in line_lower and "couponcode" not in line_lower and "图片" not in line:
                if summary_re.search(stripped) or "###" in stripped or "领券结果" in stripped:
                    clean_header = clean_text(stripped.lstrip("#"))
                    header_lines.append(clean_header)
                    continue
            
            # Parse coupon details
            # Structure typically looks like:
            # - Coupon Name
            # - couponId: ...
            # - couponCode: ...
            if stripped.startswith(("- ", "* ", "+ ")):
                in_coupon_section = True
                content = stripped[2:].strip()
                
                lower_content = content.lower()
                if "couponid" in lower_content or "couponcode" in lower_content:
                    continue

                if "<img" in content:
                    m = img_re.search(content)
                    if m:
                        current_coupon['image'] = m.group(1)
                    if "图片" in content or lower_content.startswith(("image", "img")):
                        continue

                # Check for key-value pair (support both : and fullwidth colon)
                parts = re.split(r"[:\uFF1A]", content, maxsplit=1)
                if len(parts) == 2:
                    key = parts[0]
                    value = parts[1]
                        
                    key = key.strip()
                    value = value.strip()
                    key_lower = key.lower()
                    
                    if "<img" in value:
                        m = img_re.search(value)
                        if m:
                            current_coupon['image'] = m.group(1)
                    
                    if "couponcode" in key_lower or key in ["券码", "券号", "兑换码"]:
                        current_coupon['code'] = clean_text(value)
                        continue
                    if "couponid" in key_lower:
                        continue
                    if key_lower in ["image", "img"] or key in ["图片"]:
                        img_url = None
                        m = img_re.search(value)
                        if m:
                            img_url = m.group(1)
                        elif value.startswith("http"):
                            img_url = value.split()[0]
                        if img_url:
                            current_coupon['image'] = img_url
                        continue
                    
                    if key in ["优惠券标题", "标题", "优惠券名称", "名称"]:
                        current_coupon['name'] = clean_text(value)
                    # ignore couponId, couponCode, 图片 etc.
                else:
                    if "<img" in content:
                        m = img_re.search(content)
                        if m:
                            current_coupon['image'] = m.group(1)
                        continue
                    # No colon, assume it's the coupon name
                    # If we already have a name, it means we missed the end of the previous coupon
                    # or this is the start of a new one. Push the previous one.
                    if current_coupon.get('name'):
                        parsed_coupons.append(current_coupon)
                        current_coupon = {}
                    current_coupon['name'] = clean_text(content)

            # Handle empty lines or section breaks as separators
            elif not stripped or "---" in stripped:
                 if current_coupon:
                    parsed_coupons.append(current_coupon)
                    current_coupon = {}

        # Don't forget the last coupon
        if current_coupon:
            parsed_coupons.append(current_coupon)
        
        # Format output
        if header_lines:
            formatted_lines.append("🎉 领券结果")
            formatted_lines.append("")
            for h in header_lines:
                formatted_lines.append(h)
            formatted_lines.append("")
        
        if parsed_coupons:
            formatted_lines.append("━━━━━━━━━━━━━━━━━━━")
            formatted_lines.append("")
            formatted_lines.append("✅ 成功领取的优惠券：")
            for coupon in parsed_coupons:
                name = coupon.get('name', '未知优惠券')
                formatted_lines.append(f"• {name}")
            formatted_lines.append("")

        if formatted_lines:
            return "\n".join(formatted_lines).strip()

        # Fallback: even if structure parsing fails, strip noisy technical fields.
        fallback_lines = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if "couponid" in lowered or "couponcode" in lowered:
                continue
            if "<img" in lowered or "图片" in stripped:
                m = img_re.search(stripped)
                if m:
                    fallback_lines.append(f"图片: {m.group(1)}")
                continue
            fallback_lines.append(clean_text(stripped.lstrip("#")))
        return "\n".join(fallback_lines).strip()

    # Check if this is an error/failure message
    is_error = any(keyword in full_text for keyword in ["失败", "错误", "Error", "error", "无可领取"])
    
    if is_error:
        # Clean up error messages - remove markdown headers, format nicely
        error_lines = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Remove markdown headers (###, ##, #)
            if stripped.startswith('#'):
                stripped = stripped.lstrip('#').strip()
                # Add emoji if it's the main error title
                if '失败' in stripped or '错误' in stripped or 'Error' in stripped:
                    stripped = f"❌ {stripped}"
            error_lines.append(stripped)
        
        # Format with separator
        if error_lines:
            SEPARATOR = "━━━━━━━━━━━━━━━━━━━"  # Define locally to avoid circular import
            result = f"{SEPARATOR}\n"
            result += "\n".join(error_lines)
            result += f"\n{SEPARATOR}"
            return result.strip()
    
    # Original logic for regular coupon lists
    current_coupon = {}
    coupons = []
    is_coupon_list = False
    
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
            
        # Detect if this is likely a coupon list output
        if "优惠券列表" in line or "优惠券标题" in line:
            is_coupon_list = True
            
        if not is_coupon_list:
            # Clean generic lines
            cleaned_line = clean_text(line)
            lines.append(cleaned_line)
            continue

        # Parse coupon fields
        if line.startswith("- 优惠券标题：") or line.startswith("优惠券标题：") or line.startswith("## "):
            # Save previous coupon if exists
            if current_coupon:
                coupons.append(current_coupon)
                current_coupon = {}

            if line.startswith("## "):
                title = line.lstrip("#").strip()
            else:
                title = line.split("：", 1)[1].strip()
            current_coupon['title'] = clean_text(title)
            
        elif line.startswith("- 状态：") or line.startswith("状态："):
            status = line.split("：", 1)[1].strip()
            current_coupon['status'] = clean_text(status)
            
        # Ignore noisy lines that只包含图片说明或纯链接
        elif "优惠券图片" in line:
            continue
        elif line.strip().startswith("http"):
            continue
            
        # Keep other text that might be relevant (but avoid duplicates)
        elif current_coupon and line != current_coupon.get('title'):
             pass # Skip simple duplicates

    # Append the last coupon
    if current_coupon:
        coupons.append(current_coupon)

    # If we successfully parsed coupons, format them nicely
    if coupons:
        # Add any header lines found before the list
        result = "\n".join(lines) + "\n\n"
        
        for i, c in enumerate(coupons, 1):
            title = c.get('title', '未知优惠券')
            # status = c.get('status', '') # Status is usually "已领取" or similar, maybe not needed if it's "available coupons"
            
            # Simple format: 1. Title
            result += f"{i}. {title}\n"
            
        return result.strip()

    # Fallback for non-coupon text (original logic optimized)
    cleaned = []
    for line in raw_lines:
        # Remove lines that are just URLs
        if line.strip().startswith("http"):
            continue
        # Global cleanup for fallback text
        l = clean_text(line)
        cleaned.append(l)
        
    return "\n".join(cleaned).strip()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _request_mcp_with_retry(headers, tool_name, arguments):
    start_ts = time.time()
    async with streamablehttp_client(MCP_SERVER_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if arguments is None:
                result = await session.call_tool(tool_name)
            else:
                result = await session.call_tool(tool_name, arguments=arguments)

    cost = time.time() - start_ts
    print(f"[MCP] tool={tool_name} finished in {cost:.1f}s")
    return result

async def call_mcp_tool(token, tool_name, arguments=None, enable_push=False, return_raw_content=False):
    if not token or token == "your_token_here":
        return "Error: Invalid Token."

    headers = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2025-06-18",
    }

    print(f"[MCP] Connecting to {MCP_SERVER_URL} tool={tool_name}...")

    try:
        result = await asyncio.wait_for(_request_mcp_with_retry(headers, tool_name, arguments), timeout=60)

        if return_raw_content:
            return result.content

        print("\nExecution Result:")
        result_message = ""
        for content in result.content:
            if content.type == "text":
                print(content.text)
                result_message += content.text + "\n"
            else:
                print(f"[{content.type}] {content}")
                result_message += f"[{content.type}] {content}\n"

        if result_message:
            result_message = cleanup_for_telegram(result_message)

        if result_message and enable_push:
            print("\nSending push notifications...")
            await push_all(result_message)

        return result_message
                    
    except Exception as e:
        raw = str(e)
        lower_raw = raw.lower()
        if any(kw in lower_raw for kw in ["401", "unauthorized", "403", "forbidden", "invalid token", "token invalid"]):
            friendly = "[TOKEN_ERROR] 麦当劳 MCP 认证失败，Token 可能已失效或无效，请重新绑定。"
        elif "429" in raw:
            friendly = "[SERVER_ERROR] 麦当劳 MCP 接口返回 429（请求过于频繁），请稍后再试。"
        else:
            friendly = "[SERVER_ERROR] 麦当劳 MCP 服务当前出现异常，可能在维护或短暂故障，请稍后再试。"
        print(f"{friendly} 详细信息：{raw}")
        return friendly

def is_mcp_token_error(text: str) -> bool:
    """判断结果是否为 Token 认证相关错误（Token 失效/无效/未授权）"""
    if not text:
        return False
    text = str(text).strip()
    lower = text.lower()
    if "[TOKEN_ERROR]" in text:
        return True
    if "Error: Invalid Token." in text:
        return True
    if any(marker in lower for marker in ["unauthorized", "forbidden", "invalid token", "token invalid"]):
        return True
    if any(marker in text for marker in ["Token 无效", "token 无效", "Token已失效", "token已失效", "未授权", "认证失败"]):
        return True
    return False

def is_mcp_server_error(text: str) -> bool:
    """判断结果是否为服务器端错误（429/500/超时/维护等，与 Token 无关）"""
    if not text:
        return False
    text = str(text).strip()
    if "[SERVER_ERROR]" in text:
        return True
    if "麦当劳 MCP 服务当前出现异常" in text:
        return True
    if "麦当劳 MCP 接口返回 429" in text:
        return True
    return False

def is_mcp_error_message(text: str) -> bool:
    """判断结果是否为任意 MCP 错误（Token 错误或服务器错误）"""
    return is_mcp_token_error(text) or is_mcp_server_error(text)

def strip_calendar_today_header(text: str) -> str:
    if not text:
        return ""
    raw_lines = text.splitlines()
    cleaned = []
    skipping_first = True
    for line in raw_lines:
        stripped = line.strip()
        if skipping_first:
            if not stripped:
                continue
            if "今天是" in stripped or "今日" in stripped:
                skipping_first = False
                continue
            skipping_first = False
        cleaned.append(line)
    return "\n".join(cleaned)

def reorder_calendar_sections(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    prefix = []
    sections = []
    current = None

    def is_header(line: str) -> bool:
        l = line.strip()
        if not l:
            return False
        if "昨日" not in l and "今日" not in l and "今天" not in l:
            return False
        return l.startswith("#") or l.startswith("【") or l.startswith("昨日") or l.startswith("今日") or l.startswith("今天")

    for line in lines:
        if is_header(line):
            if current:
                sections.append(current)
            current = [line]
        else:
            if current is None:
                prefix.append(line)
            else:
                current.append(line)

    if current:
        sections.append(current)

    if not sections:
        return text

    idx_today = None
    idx_yesterday = None
    for i, sec in enumerate(sections):
        header = sec[0].strip()
        if idx_today is None and ("今日" in header or "今天" in header):
            idx_today = i
        if idx_yesterday is None and "昨日" in header:
            idx_yesterday = i

    if idx_today is None or idx_yesterday is None:
        return text

    if idx_yesterday < idx_today:
        new_sections = []
        for i, sec in enumerate(sections):
            if i == idx_yesterday:
                new_sections.append(sections[idx_today])
            elif i == idx_today:
                new_sections.append(sections[idx_yesterday])
            else:
                new_sections.append(sec)
        merged = prefix + [line for sec in new_sections for line in sec]
        return "\n".join(merged)

    return text

def remove_yesterday_section(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    cleaned = []
    skipping = False

    def is_header(line: str) -> bool:
        l = line.strip()
        if not l:
            return False
        if l.startswith(("#", "【", "昨日", "昨天", "今日", "今天", "明日", "明天")):
            return True
        return False

    for line in lines:
        stripped = line.strip()
        if is_header(line):
            if "昨日" in stripped or "昨天" in stripped:
                skipping = True
                continue
            skipping = False
        if skipping:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)

async def claim_for_token(token, enable_push=True):
    return await call_mcp_tool(token, "auto-bind-coupons", enable_push=enable_push)

async def list_available_coupons(token):
    return await call_mcp_tool(token, "available-coupons", enable_push=False)

async def list_my_coupons(token, return_raw=False):
    """
    获取我的优惠券
    
    Args:
        token: MCP Token
        return_raw: 如果为True，返回原始数据（包含有效期信息）；否则返回清理后的数据
    """
    if return_raw:
        # 直接返回原始内容，不经过cleanup（保留有效期信息）
        return await call_mcp_tool(token, "my-coupons", enable_push=False, return_raw_content=True)
    else:
        # 返回清理后的内容（用于显示给用户）
        return await call_mcp_tool(token, "my-coupons", enable_push=False)

async def list_campaign_calendar(token, date=None, return_raw=False):
    arguments = None
    if date:
        arguments = {"specifiedDate": date}
    
    if return_raw:
        content_list = await call_mcp_tool(token, "campaign-calender", arguments=arguments, enable_push=False, return_raw_content=True)
        if isinstance(content_list, str): # Error message
            return content_list
            
        # Try to parse JSON from the first text content
        import json
        for content in content_list:
            if content.type == 'text':
                try:
                    data = json.loads(content.text)
                    return data
                except json.JSONDecodeError:
                    pass
        
        # Fallback: return text if parsing fails
        text_result = ""
        for content in content_list:
            if content.type == 'text':
                text_result += content.text
        return text_result

    return await call_mcp_tool(token, "campaign-calender", arguments=arguments, enable_push=False)

from quotes import MCD_QUOTES
from datetime import datetime, timedelta, timezone

async def get_today_recommendation(token):
    if not token or token == "your_token_here":
        return "Error: Invalid Token."
    
    # Force China Standard Time (UTC+8) calculation
    cst_now = get_cst_now()
    today = cst_now.strftime("%Y-%m-%d")
    current_hour = cst_now.hour
   
    calendar_text, available_text = await asyncio.gather(
        list_campaign_calendar(token, date=today),
        list_available_coupons(token)
    )
    
    lines = []
    lines.append(f"📅 {today}")
    
    # 1. 高亮推荐逻辑
    highlights = []
    if available_text:
        # 简单关键词匹配
        if "免费" in available_text or "0元" in available_text:
            highlights.append("✨ 发现免费羊毛！赶紧看看列表！")
        if "买一送一" in available_text or "1+1" in available_text:
            highlights.append("🔥 有买一送活动！适合找人拼单")
        if "半价" in available_text:
            highlights.append("💰 半价优惠！四舍五入不要钱")
    
    if highlights:
        lines.append("")
        lines.append("\n".join(highlights))

    # 2. 时段推荐逻辑
    time_tip = ""
    if 5 <= current_hour < 10:
        time_tip = "🍳 早餐时段：来个猪柳蛋堡唤醒灵魂吧"
    elif 11 <= current_hour < 14:
        time_tip = "🍔 午餐时段：1+1随心配，最强穷鬼套餐"
    elif 14 <= current_hour < 17:
        time_tip = "☕ 下午茶时段：工作累了？点杯咖啡配个派"
    elif 17 <= current_hour < 21:
        time_tip = "🍗 晚餐时段：今晚吃顿好的，对自己好一点"
    elif 21 <= current_hour or current_hour < 5:
        time_tip = "🌙 夜宵时段：虽然会胖，但是炸鸡真香啊"
        
    if time_tip:
        lines.append("")
        lines.append(time_tip)

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("【今天的活动】")
    calendar_error = False
    if not calendar_text:
        calendar_error = True
        lines.append("暂未查询到当日活动信息。")
    else:
        if is_mcp_error_message(calendar_text):
            calendar_error = True
            lines.append("查询活动信息时出现问题：")
            lines.append(calendar_text.strip())
        else:
            cal_cleaned = strip_calendar_today_header(calendar_text)
            # Remove raw Markdown bold syntax like **Title** and trailing backslashes
            cal_cleaned = cal_cleaned.replace("**", "").replace("\\", "")
            cal_cleaned = reorder_calendar_sections(cal_cleaned)
            cal_cleaned = remove_yesterday_section(cal_cleaned)
            lines.append(cal_cleaned.strip())
    
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("【你当前可领的优惠券】")
    available_error = False
    if not available_text:
        available_error = True
        lines.append("暂未查询到可领券。")
    else:
        if is_mcp_error_message(available_text):
            available_error = True
            lines.append("查询可领优惠券时出现问题：")
            lines.append(available_text.strip())
        else:
            lines.append(available_text.strip())
    lines.append("")
    
    if calendar_error and available_error:
        lines.append("当前暂时无法获取活动或优惠券的正常信息，可能是 MCP 服务短暂异常或网络问题，可以稍后再试一次。")
    else:
        # 随机一句麦门文学
        quote = random.choice(MCD_QUOTES)
        lines.append(f"🍟 {quote}")
        
    return "\n".join(lines)

async def main():
    token = os.getenv("MCD_MCP_TOKEN")
    if not token:
        print("Error: Please set MCD_MCP_TOKEN in .env file")
        return
    await claim_for_token(token, enable_push=True)

async def run_task():
    cst_now = get_cst_now()
    print(f"\n[{cst_now.strftime('%Y-%m-%d %H:%M:%S')}] Starting scheduled task...")
    await main()

def job():
    asyncio.run(run_task())

if __name__ == "__main__":
    # Check if loop mode is enabled
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        print("Starting in loop mode. Will run daily at 10:30 AM.")
        # Also run immediately on startup
        job()
        last_run_date = None
        while True:
            cst_now = get_cst_now()
            if cst_now.hour == 10 and cst_now.minute == 30:
                if last_run_date != cst_now.date():
                    job()
                    last_run_date = cst_now.date()
            time.sleep(30)
    else:
        asyncio.run(main())
