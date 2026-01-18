import os
import httpx
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Telegram notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")

async def send_bark(key, message):
    # Bark format: https://api.day.app/{key}/{title}/{body}
    # Using title as "McDonalds Coupon"
    url = f"https://api.day.app/{key}/McDonalds_Coupon/{message}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            logger.info("Bark notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Bark notification: {e}")

async def send_feishu(webhook_url, message):
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"McDonalds Coupon Report:\n{message}"
        }
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("Feishu notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send Feishu notification: {e}")

async def send_serverchan(sendkey, message):
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = {
        "title": "McDonalds Coupon Report",
        "desp": message
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=payload)
            resp.raise_for_status()
            logger.info("ServerChan notification sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send ServerChan notification: {e}")

async def push_all(message):
    """
    Checks environment variables and sends notifications to all configured services.
    """
    tasks = []
    
    # Telegram
    tg_token = os.getenv("TG_BOT_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    if tg_token and tg_chat_id:
        tasks.append(send_telegram(tg_token, tg_chat_id, message))
        
    # Bark
    bark_key = os.getenv("BARK_KEY")
    if bark_key:
        tasks.append(send_bark(bark_key, message))
        
    # Feishu
    feishu_webhook = os.getenv("FEISHU_WEBHOOK")
    if feishu_webhook:
        tasks.append(send_feishu(feishu_webhook, message))
        
    # ServerChan
    serverchan_key = os.getenv("SERVERCHAN_SENDKEY")
    if serverchan_key:
        tasks.append(send_serverchan(serverchan_key, message))

    if tasks:
        await httpx.ASGITransport(app=None) # Just to ensure imports work if needed, mostly dummy here
        import asyncio
        await asyncio.gather(*tasks)
    else:
        logger.info("No notification services configured. Skipping push.")

if __name__ == "__main__":
    # Test
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(push_all("Test message from McDonalds Script"))
