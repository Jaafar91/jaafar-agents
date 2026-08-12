import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, telegram_admin_chat_ids, is_placeholder
from database import get_db, init_db
from mobile_config import read_config, set_enabled, set_message
from telegram_utils import TelegramUpdate, send_reply

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_openai_bot")

TELEGRAM_CONFIGURED = not is_placeholder(TELEGRAM_BOT_TOKEN) and not is_placeholder(TELEGRAM_CHAT_ID)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Jaafar Agents", lifespan=lifespan)


def is_authorized(chat_id: int | None) -> bool:
    return chat_id is not None and str(chat_id) in telegram_admin_chat_ids()


def command_reply(text: str, db: Session) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    if command == "/help":
        return "Commands:\n/config — show current app configuration\n/setmessage <text> — change the app message\n/enable — enable the app feature\n/disable — disable the app feature"
    if command == "/config":
        config = read_config(db)
        return f"Message: {config.message}\nEnabled: {config.enabled}"
    if command == "/setmessage":
        if not argument.strip():
            return "Usage: /setmessage Your new app message"
        config = set_message(db, argument)
        return f"App message updated: {config.message}"
    if command == "/enable":
        set_enabled(db, True)
        return "App feature enabled."
    if command == "/disable":
        set_enabled(db, False)
        return "App feature disabled."
    return None


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "telegram_configured": TELEGRAM_CONFIGURED,
        "telegram_admin_configured": bool(telegram_admin_chat_ids()),
    }


@app.get("/api/v1/mobile/config")
def mobile_config(db: Session = Depends(get_db)):
    config = read_config(db)
    return {"message": config.message, "enabled": config.enabled}


@app.post("/telegram/webhook")
def telegram_webhook(update: TelegramUpdate, db: Session = Depends(get_db)):
    message = update.message
    text = message.text.strip() if message and message.text else ""
    chat_id = message.chat.id if message and message.chat else None

    if not text:
        return {"ok": False, "error": "No message text provided"}
    if not is_authorized(chat_id):
        logger.warning("Rejected Telegram message from unauthorized chat")
        raise HTTPException(status_code=403, detail="Unauthorized Telegram chat")

    reply = command_reply(text, db)
    if reply is None:
        reply = "Unsupported command. Send /help for available commands."

    if not TELEGRAM_CONFIGURED:
        return {"ok": False, "error": "Telegram credentials are not configured"}

    send_reply(logger, chat_id, reply)
    return {"ok": True, "reply": reply}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "9000")))
