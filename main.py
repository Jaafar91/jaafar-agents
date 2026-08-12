import json
import logging

import requests
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from config import GITHUB_TOKEN, MOBILE_APP_REPOSITORY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, is_placeholder, telegram_admin_chat_ids
from database import get_db, init_db
from github_webhook import notification_text, send_notification, signature_is_valid
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


def create_feature_request_in_background(chat_id: int, request: str) -> None:
    if is_placeholder(GITHUB_TOKEN):
        send_reply(logger, chat_id, "GitHub is not configured. Set GITHUB_TOKEN in Render.")
        return

    response = requests.post(
        "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/issues",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + GITHUB_TOKEN,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "title": "Telegram feature: " + request[:72],
            "body": (
                "Feature request received from an authorized Telegram administrator.\n\n"
                "## Request\n" + request + "\n\n"
                "Create and test a pull request from this issue. Do not merge until the Android build passes."
            ),
        },
        timeout=20,
    )
    if response.status_code not in {200, 201}:
        logger.error("GitHub issue creation failed: %s %s", response.status_code, response.text)
        send_reply(logger, chat_id, "GitHub could not create the feature request. Check the Render logs.")
        return

    issue = response.json()
    send_reply(
        logger,
        chat_id,
        "Feature request #" + str(issue["number"]) + " created:\n" + issue["html_url"] +
        "\n\nI will also send PR and Android build updates here after the GitHub webhook is configured.",
    )


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
            "/feature <request> — create a GitHub feature request"
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
            background_tasks.add_task(create_feature_request_in_background, chat_id, argument.strip())
            reply = "Feature request received. I will send the GitHub issue link here shortly."
    else:
        reply = command_reply(text, db)
        if reply is None:
            reply = "Unsupported command. Send /help for available commands."

    send_reply(logger, chat_id, reply)
    return {"ok": True, "reply": reply}


@app.post("/github/webhook", status_code=204)
async def github_webhook(
    request: Request,
    x_github_event: str = Header(...),
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()
    if not signature_is_valid(body, x_hub_signature_256):
        logger.warning("Rejected GitHub webhook with an invalid signature")
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub webhook payload") from exc

    text = notification_text(x_github_event, payload)
    if text:
        send_notification(logger, text)

    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "9000")))
