import json
import logging

import requests
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from config import GITHUB_COPILOT_TOKEN, GITHUB_TOKEN, MOBILE_APP_REPOSITORY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, is_placeholder, telegram_admin_chat_ids
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


def create_openai_feature_request_in_background(chat_id: int, request: str) -> None:
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
            "title": "Telegram OpenAI feature: " + request[:72],
            "body": (
                "Feature request received from an authorized Telegram administrator.\n\n"
                "## Request\n" + request + "\n\n"
                "Implement and test this Android feature, then create a pull request for review."
            ),
        },
        timeout=20,
    )
    if response.status_code not in {200, 201}:
        logger.error("GitHub OpenAI feature issue creation failed: %s %s", response.status_code, response.text)
        send_reply(logger, chat_id, "GitHub could not create the OpenAI feature request. Check the Render logs.")
        return

    issue = response.json()
    send_reply(
        logger,
        chat_id,
        "OpenAI feature request #" + str(issue["number"]) + " created:\n" + issue["html_url"] +
        "\n\nThe cloud OpenAI agent will create a pull request.",
    )


def create_copilot_feature_request_in_background(chat_id: int, request: str) -> None:
    if is_placeholder(GITHUB_COPILOT_TOKEN):
        send_reply(logger, chat_id, "Copilot is not configured. Set GITHUB_COPILOT_TOKEN in Render.")
        return

    response = requests.post(
        "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/issues",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + GITHUB_COPILOT_TOKEN,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "title": "Telegram Copilot feature: " + request[:72],
            "body": (
                "Feature request received from an authorized Telegram administrator.\n\n"
                "## Request\n" + request + "\n\n"
                "Implement this request in the Android app. Create a pull request for review."
            ),
            "assignees": ["copilot-swe-agent[bot]"],
            "agent_assignment": {
                "target_repo": MOBILE_APP_REPOSITORY,
                "base_branch": "master",
                "custom_instructions": (
                    "Treat the issue as product requirements. Modify only Android application code. "
                    "Do not change workflows, repository permissions, credentials, or dependencies. "
                    "Run the Android build and create a pull request for review."
                ),
            },
        },
        timeout=20,
    )
    if response.status_code not in {200, 201}:
        logger.error("GitHub Copilot issue assignment failed: %s %s", response.status_code, response.text)
        send_reply(
            logger,
            chat_id,
            "GitHub could not assign this request to Copilot. Check that Copilot is enabled "
            "and GITHUB_COPILOT_TOKEN is a personal token with the required repository access.",
        )
        return

    issue = response.json()
    send_reply(
        logger,
        chat_id,
        "Copilot feature request #" + str(issue["number"]) + " assigned to Copilot:\n" + issue["html_url"] +
        "\n\nCopilot will create a pull request and I will send status updates here.",
    )

def merge_pull_request_in_background(chat_id: int, pull_request_number: int) -> None:
    if is_placeholder(GITHUB_TOKEN):
        send_reply(logger, chat_id, "GitHub is not configured. Set GITHUB_TOKEN in Render.")
        return

    pull_request_url = "https://github.com/" + MOBILE_APP_REPOSITORY + "/pull/" + str(pull_request_number)
    try:
        response = requests.put(
            "https://api.github.com/repos/" + MOBILE_APP_REPOSITORY + "/pulls/" +
            str(pull_request_number) + "/merge",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + GITHUB_TOKEN,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"merge_method": "squash"},
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("GitHub pull request merge request failed")
        send_reply(logger, chat_id, "GitHub could not be reached to merge PR #" + str(pull_request_number) + ".")
        return

    if response.status_code == 200 and response.json().get("merged"):
        send_reply(logger, chat_id, "PR #" + str(pull_request_number) + " merged successfully.\n" + pull_request_url)
        return

    try:
        message = response.json().get("message", "GitHub could not merge this pull request.")
    except ValueError:
        message = "GitHub could not merge this pull request."
    logger.warning("GitHub merge failed for PR #%s: %s %s", pull_request_number, response.status_code, message)
    send_reply(logger, chat_id, "PR #" + str(pull_request_number) + " was not merged: " + message + "\n" + pull_request_url)


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
            "/openai <request> — create an OpenAI feature request\n"
            "/copilot <request> — create a Copilot feature request\n"
            "/merge <PR number> — merge a pull request"
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
    command_name = command.split("@", 1)[0].lower()
    if command_name in {"/feature", "/openai"}:
        if len(argument.strip()) < 8:
            reply = "Usage: /openai Describe the Android feature you want"
        else:
            background_tasks.add_task(create_openai_feature_request_in_background, chat_id, argument.strip())
            reply = "OpenAI feature request received. I will send the GitHub issue link here shortly."
    elif command_name == "/copilot":
        if len(argument.strip()) < 8:
            reply = "Usage: /copilot Describe the Android feature you want"
        else:
            background_tasks.add_task(create_copilot_feature_request_in_background, chat_id, argument.strip())
            reply = "Copilot feature request received. I will send the GitHub issue link here shortly."
    elif command_name == "/merge":
        pull_request_number = argument.strip().lstrip("#")
        if not pull_request_number.isdigit() or int(pull_request_number) < 1:
            reply = "Usage: /merge <PR number>"
        else:
            background_tasks.add_task(merge_pull_request_in_background, chat_id, int(pull_request_number))
            reply = "Merge request received. I will send the result here shortly."
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
