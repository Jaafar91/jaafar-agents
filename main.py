import json
import logging
from contextlib import asynccontextmanager

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from config import (
    GITHUB_COPILOT_TOKEN,
    GITHUB_TOKEN,
    MOBILE_APP_REPOSITORY,
    QUOTATION_APP_REPOSITORY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    is_placeholder,
    telegram_admin_chat_ids,
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


def create_feature_request_in_background(
    chat_id: int,
    request: str,
    repository: str,
    app_name: str,
    provider: str,
) -> None:
    token = GITHUB_COPILOT_TOKEN if provider == "Copilot" else GITHUB_TOKEN
    if is_placeholder(token):
        send_reply(logger, chat_id, f"{provider} is not configured. Check its GitHub token in Render.")
        return

    body = (
        "Feature request received from an authorized Telegram administrator.\n\n"
        "## Request\n" + request + "\n\n"
        f"Implement and test this {app_name} feature, then create a pull request for review."
    )
    payload = {
        "title": f"Telegram {provider} feature: " + request[:72],
        "body": body,
    }
    if provider == "Copilot":
        payload.update({
            "assignees": ["copilot-swe-agent[bot]"],
            "agent_assignment": {
                "target_repo": repository,
                "base_branch": "master",
                "custom_instructions": (
                    "Treat the issue as product requirements. Modify only application code. "
                    "Do not change workflows, repository permissions, credentials, or deployment configuration. "
                    "Run the appropriate build and create a pull request for review."
                ),
            },
        })

    try:
        response = requests.post(
            "https://api.github.com/repos/" + repository + "/issues",
            headers=github_headers(token),
            json=payload,
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("GitHub feature issue creation failed")
        send_reply(logger, chat_id, f"GitHub could not be reached to create the {app_name} request.")
        return

    if response.status_code not in {200, 201}:
        logger.error("GitHub feature issue creation failed: %s %s", response.status_code, response.text)
        send_reply(logger, chat_id, f"GitHub could not create the {app_name} request. Check Render logs.")
        return

    issue = response.json()
    send_reply(
        logger,
        chat_id,
        f"{app_name} {provider} feature request #{issue['number']} created:\n{issue['html_url']}\n\n"
        "The coding agent will create a pull request and GitHub will send updates here.",
    )


def merge_pull_request_in_background(chat_id: int, pull_request_number: int, repository: str, app_name: str) -> None:
    if is_placeholder(GITHUB_TOKEN):
        send_reply(logger, chat_id, "GitHub is not configured. Set GITHUB_TOKEN in Render.")
        return

    pull_request_url = f"https://github.com/{repository}/pull/{pull_request_number}"
    try:
        response = requests.put(
            f"https://api.github.com/repos/{repository}/pulls/{pull_request_number}/merge",
            headers=github_headers(GITHUB_TOKEN),
            json={"merge_method": "squash"},
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("GitHub pull request merge request failed")
        send_reply(logger, chat_id, f"GitHub could not be reached to merge {app_name} PR #{pull_request_number}.")
        return

    if response.status_code == 200 and response.json().get("merged"):
        send_reply(logger, chat_id, f"{app_name} PR #{pull_request_number} merged successfully.\n{pull_request_url}")
        return

    try:
        message = response.json().get("message", "GitHub could not merge this pull request.")
    except ValueError:
        message = "GitHub could not merge this pull request."
    logger.warning("GitHub merge failed for %s PR #%s: %s", app_name, pull_request_number, message)
    send_reply(logger, chat_id, f"{app_name} PR #{pull_request_number} was not merged: {message}\n{pull_request_url}")


def command_reply(text: str, db: Session) -> str | None:
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    if command == "/help":
        return (
            "Font Creator commands:\n"
            "/config — show current configuration\n"
            "/setmessage <text> — change the app message\n"
            "/enable or /disable — enable or disable the app feature\n"
            "/openai <request> — create a Font Creator OpenAI request\n"
            "/copilot <request> — create a Font Creator Copilot request\n"
            "/merge <PR number> — merge a Font Creator pull request\n\n"
            "Quick Quote commands:\n"
            "/quote <request> — create a Quick Quote OpenAI request\n"
            "/quote copilot <request> — create a Quick Quote Copilot request\n"
            "/quote merge <PR number> — merge a Quick Quote pull request"
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


def valid_pr_number(value: str) -> int | None:
    number = value.strip().lstrip("#")
    return int(number) if number.isdigit() and int(number) > 0 else None


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
        "font_creator_repository": MOBILE_APP_REPOSITORY,
        "quick_quote_repository": QUOTATION_APP_REPOSITORY,
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
    argument = argument.strip()

    if command_name in {"/feature", "/openai"}:
        if len(argument) < 8:
            reply = "Usage: /openai Describe the Font Creator feature you want"
        else:
            background_tasks.add_task(
                create_feature_request_in_background, chat_id, argument,
                MOBILE_APP_REPOSITORY, "Font Creator", "OpenAI"
            )
            reply = "Font Creator OpenAI feature request received. I will send the issue link shortly."
    elif command_name == "/copilot":
        if len(argument) < 8:
            reply = "Usage: /copilot Describe the Font Creator feature you want"
        else:
            background_tasks.add_task(
                create_feature_request_in_background, chat_id, argument,
                MOBILE_APP_REPOSITORY, "Font Creator", "Copilot"
            )
            reply = "Font Creator Copilot feature request received. I will send the issue link shortly."
    elif command_name == "/merge":
        number = valid_pr_number(argument)
        if number is None:
            reply = "Usage: /merge <Font Creator PR number>"
        else:
            background_tasks.add_task(
                merge_pull_request_in_background, chat_id, number,
                MOBILE_APP_REPOSITORY, "Font Creator"
            )
            reply = "Font Creator merge request received. I will send the result shortly."
    elif command_name == "/quote":
        quote_action, _, quote_request = argument.partition(" ")
        action = quote_action.lower()
        if action == "merge":
            number = valid_pr_number(quote_request)
            if number is None:
                reply = "Usage: /quote merge <Quick Quote PR number>"
            else:
                background_tasks.add_task(
                    merge_pull_request_in_background, chat_id, number,
                    QUOTATION_APP_REPOSITORY, "Quick Quote"
                )
                reply = "Quick Quote merge request received. I will send the result shortly."
        elif action in {"copilot", "openai"}:
            provider = "Copilot" if action == "copilot" else "OpenAI"
            if len(quote_request.strip()) < 8:
                reply = f"Usage: /quote {action} Describe the Quick Quote feature you want"
            else:
                background_tasks.add_task(
                    create_feature_request_in_background, chat_id, quote_request.strip(),
                    QUOTATION_APP_REPOSITORY, "Quick Quote", provider
                )
                reply = f"Quick Quote {provider} feature request received. I will send the issue link shortly."
        elif len(argument) < 8:
            reply = "Usage: /quote Describe the Quick Quote feature you want"
        else:
            background_tasks.add_task(
                create_feature_request_in_background, chat_id, argument,
                QUOTATION_APP_REPOSITORY, "Quick Quote", "OpenAI"
            )
            reply = "Quick Quote OpenAI feature request received. I will send the issue link shortly."
    else:
        reply = command_reply(text, db) or "Unsupported command. Send /help for available commands."

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
