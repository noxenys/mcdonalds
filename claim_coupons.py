import asyncio
import os
import sys
import time
import re
import schedule
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from notify import push_all

load_dotenv()

MCP_SERVER_URL = "https://mcp.mcd.cn/mcp-servers/mcd-mcp"

def cleanup_for_telegram(text):
    lines = []
    for line in text.splitlines():
        if "<img" in line:
            src_match = re.search(r'src="([^"]+)"', line)
            alt_match = re.search(r'alt="([^"]*)"', line)
            url = src_match.group(1) if src_match else ""
            alt_text = alt_match.group(1) if alt_match else ""
            if url:
                if alt_text:
                    line = f"- 图片：{alt_text} {url}"
                else:
                    line = f"- 图片：{url}"
            else:
                line = line.split("<img")[0].rstrip()
        lines.append(line)
    cleaned = []
    prev_blank = False
    for line in lines:
        if line.strip() == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()

async def call_mcp_tool(token, tool_name, arguments=None, enable_push=False, return_raw_content=False):
    if not token or token == "your_token_here":
        return "Error: Invalid Token."

    headers = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2025-06-18",
    }

    print(f"Connecting to McDonald's MCP Server at {MCP_SERVER_URL}...")

    try:
        async def _request_mcp():
            async with streamablehttp_client(MCP_SERVER_URL, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    if arguments is None:
                        return await session.call_tool(tool_name)
                    else:
                        return await session.call_tool(tool_name, arguments=arguments)

        # Set 60s timeout for the MCP interaction
        result = await asyncio.wait_for(_request_mcp(), timeout=60)

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
        if "429" in raw:
            friendly = f"麦当劳 MCP 接口返回 429（请求过于频繁），请稍后再试。\n详细信息：{raw}"
        else:
            friendly = f"麦当劳 MCP 服务当前出现异常，可能在维护或短暂故障，请稍后再试。\n详细信息：{raw}"
        print(friendly)
        return friendly

async def claim_for_token(token, enable_push=True):
    return await call_mcp_tool(token, "auto-bind-coupons", enable_push=enable_push)

async def list_available_coupons(token):
    return await call_mcp_tool(token, "available-coupons", enable_push=False)

async def list_my_coupons(token):
    return await call_mcp_tool(token, "my-coupons", enable_push=False)

async def list_campaign_calendar(token, date=None, return_raw=False):
    arguments = None
    if date:
        arguments = {"date": date}
    
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
import random

async def get_today_recommendation(token):
    if not token or token == "your_token_here":
        return "Error: Invalid Token."
    today = time.strftime("%Y-%m-%d")
    current_hour = int(time.strftime("%H"))
    
    calendar_text = await list_campaign_calendar(token, today)
    available_text = await list_available_coupons(token)
    
    lines = []
    lines.append(f"📅 今天是 {today}")
    lines.append("")
    
    # 1. 高亮推荐逻辑
    highlights = []
    if available_text:
        # 简单关键词匹配
        if "免费" in available_text or "0元" in available_text:
            highlights.append("✨ **发现免费羊毛！** 赶紧看看列表！")
        if "买一送一" in available_text or "1+1" in available_text:
            highlights.append("🔥 **有买一送一活动！** 适合找人拼单。")
        if "半价" in available_text:
            highlights.append("💰 **半价优惠！** 四舍五入不要钱。")
    
    if highlights:
        lines.append("\n".join(highlights))
        lines.append("")

    # 2. 时段推荐逻辑
    time_tip = ""
    if 5 <= current_hour < 10:
        time_tip = "🍳 **早餐时段**：来个猪柳蛋堡唤醒灵魂吧！"
    elif 11 <= current_hour < 14:
        time_tip = "🍔 **午餐时段**：1+1随心配，最强穷鬼套餐。"
    elif 14 <= current_hour < 17:
        time_tip = "☕ **下午茶时段**：工作累了？点杯咖啡配个派。"
    elif 17 <= current_hour < 21:
        time_tip = "🍗 **晚餐时段**：今晚吃顿好的，对自己好一点。"
    elif 21 <= current_hour or current_hour < 5:
        time_tip = "🌙 **夜宵时段**：虽然会胖，但是炸鸡真香啊..."
        
    if time_tip:
        lines.append(time_tip)
        lines.append("")

    lines.append("【今天的活动】")
    calendar_error = False
    if not calendar_text:
        calendar_error = True
        lines.append("暂未查询到当日活动信息。")
    else:
        cal_lower = calendar_text.lower()
        if "error" in cal_lower or "401" in calendar_text or "unauthorized" in cal_lower:
            calendar_error = True
            lines.append("查询活动信息时出现问题：")
            lines.append(calendar_text.strip())
        else:
            lines.append(calendar_text.strip())
    lines.append("")
    lines.append("【你当前可领的优惠券】")
    available_error = False
    if not available_text:
        available_error = True
        lines.append("暂未查询到可领券。")
    else:
        avl_lower = available_text.lower()
        if "error" in avl_lower or "401" in available_text or "unauthorized" in avl_lower:
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
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting scheduled task...")
    await main()

def job():
    asyncio.run(run_task())

if __name__ == "__main__":
    # Check if loop mode is enabled
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        print("Starting in loop mode. Will run daily at 10:30 AM.")
        # Schedule the job every day at 10:30 AM
        schedule.every().day.at("10:30").do(job)
        
        # Also run immediately on startup
        job()
        
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        asyncio.run(main())
