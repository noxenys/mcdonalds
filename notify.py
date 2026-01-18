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

