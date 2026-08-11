from pydantic import BaseModel
from typing import Optional
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramChat(BaseModel):
    id: int
    username: Optional[str] = None


class TelegramMessage(BaseModel):
    message_id: int
    text: Optional[str] = None
    chat: Optional[TelegramChat] = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None


def send_reply(logger, chat_id, reply):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        raise RuntimeError("Telegram bot credentials are not configured")

    payload = {"chat_id": chat_id, "text": reply}
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json=payload,
        timeout=10,
    )
    logger.info("Telegram API response: %s %s", response.status_code, response.text)
    if response.status_code != 200:
        raise RuntimeError(response.text)
