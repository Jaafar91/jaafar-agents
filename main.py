import json
import logging
from contextlib import asynccontextmanager

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from config import (
    GITHUB_COPILOT_TOKEN, GITHUB_TOKEN, IOS_FONT_CREATOR_REPOSITORY,
    MOBILE_APP_REPOSITORY, QUOTATION_APP_REPOSITORY, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID, is_placeholder, telegram_admin_chat_ids,
)
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


def github_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_feature_request(chat_id: int, request: str, repository: str, app_name: str, provider: str) -> None:
    token = GITHUB_COPILOT_TOKEN if provider == "Copilot" else GITHUB_TOKEN
    if is_placeholder(token):
        send_reply(logger, chat_id, f"{provider} is not configured. Check its GitHub token in Render.")
        return
    payload = {
        "title": f"Telegram {provider} feature: " + request[:72],
        "body": (
            "Feature request received from an authorized Telegram administrator.\n\n"
            f"## Request\n{request}\n\n"
            f"Implement and test this {app_name} feature, then create a pull request for review."
        ),
    }
    if provider == "Copilot":
        payload["assignees"] = ["copilot-swe-agent[bot]"]
        payload["agent_assignment"] = {
            "target_repo": repository,
            "base_branch": "master",
            "custom_instructions": (
                "Treat the issue as product requirements. Modify only application code. "
                "Do not change workflows, credentials, permissions, or deployment configuration. "
                "Run the appropriate build and create a pull request for review."
            ),
        }
    try:
        response = requests.post(
            f"https://api.github.com/repos/{repository}/issues",
            headers=github_headers(token), json=payload, timeout=20,
        )
    except requests.RequestException:
        logger.exception("GitHub issue creation failed")
        send_reply(logger, chat_id, f"GitHub could not be reached to create the {app_name} request.")
        return
    if response.status_code not in {200, 201}:
        logger.error("GitHub issue creation failed: %s %s", response.status_code, response.text)
        send_reply(logger, chat_id, f"GitHub could not create the {app_name} request. Check Render logs.")
        return
    issue = response.json()
    send_reply(
        logger, chat_id,
        f"{app_name} {provider} feature request #{issue['number']} created:\n{issue['html_url']}\n\n"
        "The coding agent will create a pull request and GitHub will send updates here.",
    )


def merge_pull_request(chat_id: int, number: int, repository: str, app_name: str) -> None:
    if is_placeholder(GITHUB_TOKEN):
        send_reply(logger, chat_id, "GitHub is not configured. Set GITHUB_TOKEN in Render.")
        return
    url = f"https://github.com/{repository}/pull/{number}"
    try:
        response = requests.put(
            f"https://api.github.com/repos/{repository}/pulls/{number}/merge",
            headers=github_headers(GITHUB_TOKEN), json={"merge_method": "squash"}, timeout=20,
        )
    except requests.RequestException:
        logger.exception("GitHub merge request failed")
        send_reply(logger, chat_id, f"GitHub could not be reached to merge {app_name} PR #{number}.")
        return
    if response.status_code == 200 and response.json().get("merged"):
        send_reply(logger, chat_id, f"{app_name} PR #{number} merged successfully.\n{url}")
        return
    try:
        message = response.json().get("message", "GitHub could not merge this pull request.")
    except ValueError:
        message = "GitHub could not merge this pull request."
    send_reply(logger, chat_id, f"{app_name} PR #{number} was not merged: {message}\n{url}")


def help_text() -> str:
    return (
        "Font Creator Android:\n"
        "/openai <request>\n/copilot <request>\n/merge <PR number>\n\n"
        "Quick Quote:\n"
        "/quote <request>\n/quote copilot <request>\n/quote merge <PR number>\n\n"
        "Font Creator iOS:\n"
        "/ios <request>\n/ios copilot <request>\n/ios merge <PR number>\n\n"
        "Other:\n/config\n/setmessage <text>\n/enable\n/disable"
    )


def command_reply(text: str, db: Session) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    if command == "/help":
        return help_text()
    if command == "/config":
        config = read_config(db)
        return "Message: " + config.message + "\nEnabled: " + str(config.enabled)
    if command == "/setmessage":
        if not argument.strip():
            return "Usage: /setmessage Your new app message"
        return "App message updated: " + set_message(db, argument).message
    if command == "/enable":
        set_enabled(db, True)
        return "App feature enabled."
    if command == "/disable":
        set_enabled(db, False)
        return "App feature disabled."
    return None


def pr_number(value: str) -> int | None:
    value = value.strip().lstrip("#")
    return int(value) if value.isdigit() and int(value) > 0 else None


def handle_target_command(command: str, argument: str, chat_id: int, background_tasks: BackgroundTasks) -> str | None:
    targets = {
        "/quote": (QUOTATION_APP_REPOSITORY, "Quick Quote"),
        "/ios": (IOS_FONT_CREATOR_REPOSITORY, "Font Creator iOS"),
    }
    if command not in targets:
        return None
    repository, app_name = targets[command]
    first, _, remainder = argument.partition(" ")
    action, request = first.lower(), remainder.strip()
    if action == "merge":
        number = pr_number(request)
        if number is None:
            return f"Usage: {command} merge <{app_name} PR number>"
        background_tasks.add_task(merge_pull_request, chat_id, number, repository, app_name)
        return f"{app_name} merge request received. I will send the result shortly."
    if action in {"openai", "copilot"}:
        provider = "Copilot" if action == "copilot" else "OpenAI"
    else:
        provider, request = "OpenAI", argument
    if len(request.strip()) < 8:
        return f"Usage: {command} [copilot] Describe the {app_name} feature you want"
    background_tasks.add_task(create_feature_request, chat_id, request.strip(), repository, app_name, provider)
    return f"{app_name} {provider} feature request received. I will send the issue link shortly."


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {
        "status": "ok", "telegram_configured": TELEGRAM_CONFIGURED,
        "telegram_admin_configured": bool(telegram_admin_chat_ids()),
        "font_creator_android_repository": MOBILE_APP_REPOSITORY,
        "quick_quote_repository": QUOTATION_APP_REPOSITORY,
        "font_creator_ios_repository": IOS_FONT_CREATOR_REPOSITORY,
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

    raw_command, _, argument = text.partition(" ")
    command, argument = raw_command.split("@", 1)[0].lower(), argument.strip()
    if command in {"/feature", "/openai", "/copilot"}:
        provider = "Copilot" if command == "/copilot" else "OpenAI"
        if len(argument) < 8:
            reply = f"Usage: {command} Describe the Font Creator Android feature you want"
        else:
            background_tasks.add_task(create_feature_request, chat_id, argument, MOBILE_APP_REPOSITORY, "Font Creator Android", provider)
            reply = "Font Creator Android feature request received. I will send the issue link shortly."
    elif command == "/merge":
        number = pr_number(argument)
        if number is None:
            reply = "Usage: /merge <Font Creator Android PR number>"
        else:
            background_tasks.add_task(merge_pull_request, chat_id, number, MOBILE_APP_REPOSITORY, "Font Creator Android")
            reply = "Font Creator Android merge request received. I will send the result shortly."
    else:
        reply = handle_target_command(command, argument, chat_id, background_tasks)
        if reply is None:
            reply = command_reply(text, db) or "Unsupported command. Send /help for available commands."
    send_reply(logger, chat_id, reply)
    return {"ok": True, "reply": reply}


@app.post("/github/webhook", status_code=204)
async def github_webhook(request: Request, x_github_event: str = Header(...), x_hub_signature_256: str | None = Header(default=None)):
    body = await request.body()
    if not signature_is_valid(body, x_hub_signature_256):
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
