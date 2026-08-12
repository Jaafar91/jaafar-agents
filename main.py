import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, is_placeholder, telegram_admin_chat_ids
from database import get_db, init_db
from feature_agent import FeatureAgent
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


def create_feature_in_background(chat_id: int, request: str) -> None:
    try:
        number, url = FeatureAgent(logger).create_draft_pr(request)
        send_reply(logger, chat_id, "Draft PR #" + str(number) + " is ready for review:\n" + url + "\n\nWhen you approve it, send /approve " + str(number) + ".")
    except Exception:
        logger.exception("Telegram feature generation failed")
        send_reply(logger, chat_id, "Feature generation failed. No PR was created; check the Render logs.")


def command_reply(text: str, db: Session) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    if command == "/help":
        return (
            "Commands:\n"
            "/config — show current app configuration\n"
            "/setmessage <text> — change the app message\n"
            "/enable — enable the app feature\n"
            "/disable — disable the app feature\n"
            "/feature <request> — create a draft Android PR\n"
            "/approve <PR number> — merge an approved PR and start the APK build"
        )
    if command == "/config":
        config = read_config(db)
        return "Message: " + config.message + "\nEnabled: " + str(config.enabled)
    if command == "/setmessage":
        if not argument.strip():
            return "Usage: /setmessage Your new app message"
        config = set_message(db, argument)
        return "App message updated: " + config.message
    if command == "/enable":
        set_enabled(db, True)
        return "App feature enabled."
    if command == "/disable":
        set_enabled(db, False)
        return "App feature disabled."
    if command == "/approve":
        if not argument.strip().isdigit():
            return "Usage: /approve <PR number>"
        try:
            actions_url = FeatureAgent(logger).approve_and_merge(int(argument.strip()))
            return "PR #" + argument.strip() + " merged. GitHub is building the APK now:\n" + actions_url
        except Exception:
            logger.exception("Telegram PR approval failed")
            return "The PR was not merged. Review it in GitHub, mark it ready, and resolve any conflicts first."
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
def telegram_webhook(update: TelegramUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    message = update.message
    text = message.text.strip() if message and message.text else ""
    chat_id = message.chat.id if message and message.chat else None

    if not text:
        return {"ok": False, "error": "No message text provided"}
    if not is_authorized(chat_id):
        logger.warning("Rejected Telegram message from unauthorized chat")
        raise HTTPException(status_code=403, detail="Unauthorized Telegram chat")
    if not TELEGRAM_CONFIGURED:
        return {"ok": False, "error": "Telegram credentials are not configured"}

    command, _, argument = text.strip().partition(" ")
    if command.split("@", 1)[0].lower() == "/feature":
        if len(argument.strip()) < 8:
            reply = "Usage: /feature Describe the Android feature you want"
        else:
            background_tasks.add_task(create_feature_in_background, chat_id, argument.strip())
            reply = "Feature request received. I will send the draft PR link here when it is ready."
    else:
        reply = command_reply(text, db)
        if reply is None:
            reply = "Unsupported command. Send /help for available commands."

    send_reply(logger, chat_id, reply)
    return {"ok": True, "reply": reply}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "9000")))
