import logging
import os
import re
from fastapi import FastAPI
from config import PORT, GITHUB_BRANCH, GITHUB_COMMIT_EMAIL, GITHUB_COMMIT_NAME, GITHUB_REPO_URL, GITHUB_TOKEN, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, is_placeholder
from openai_utils import OpenAIClient
from github_utils import create_commit_and_push, delete_file_and_push
from telegram_utils import TelegramUpdate, send_reply

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_openai_bot")

OPENAI_CONFIGURED = not is_placeholder(OPENAI_API_KEY)
TELEGRAM_CONFIGURED = not is_placeholder(TELEGRAM_BOT_TOKEN) and not is_placeholder(TELEGRAM_CHAT_ID)

app = FastAPI(title="Telegram Webhook Receiver")
openai_client = OpenAIClient()


def is_delete_request(text):
    lowered = (text or "").lower()
    if any(token in lowered for token in ["delete", "remove", "drop", "erase", "clear"]):
        return not any(phrase in lowered for phrase in ["don't delete", "do not delete", "not delete", "not remove"])
    return False


def extract_delete_target(text):
    if not is_delete_request(text):
        return None

    match = re.search(r"\b(?:delete|remove|drop|erase|clear)\s+(?:file\s+)?(?:named\s+)?([a-zA-Z0-9._/-]+)", (text or "").lower())
    if not match:
        return None

    target = match.group(1).strip()
    if target in {"repo", "repository", "project"}:
        return None
    return target


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def root():
    return {"status": "ok"}


@app.post("/telegram/webhook")
def telegram_webhook(update: TelegramUpdate):
    text = update.message.text if update.message else None
    logger.info("Received Telegram update: %s", update.dict())

    if not text:
        logger.warning("No message text provided")
        return {"ok": False, "error": "No message text provided"}

    logger.info("Incoming message text: %s", text)

    if not OPENAI_CONFIGURED:
        logger.error("OpenAI API key is not configured")
        return {"ok": False, "error": "OpenAI API key is not configured. Set OPENAI_API_KEY in the .env file."}

    try:
        reply = openai_client.get_reply(text)
        logger.info("OpenAI reply: %s", reply)
    except Exception as exc:
        logger.exception("OpenAI request failed")
        return {"ok": False, "error": f"OpenAI request failed: {exc}"}

    chat_id = None
    if update.message and update.message.chat:
        chat_id = update.message.chat.id
    if not chat_id and TELEGRAM_CHAT_ID:
        chat_id = TELEGRAM_CHAT_ID

    if TELEGRAM_CONFIGURED:
        try:
            send_reply(logger, chat_id, reply)
        except Exception as exc:
            logger.exception("Telegram send failed")
            return {"ok": False, "error": f"Telegram send failed: {exc}"}
    else:
        logger.error("Telegram bot credentials are not configured")
        return {"ok": False, "error": "Telegram bot credentials are not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the .env file."}

    github_link = None
    delete_target = extract_delete_target(text)
    if GITHUB_REPO_URL:
        repo_dir = os.path.join(os.getcwd(), "repo")
        os.makedirs(repo_dir, exist_ok=True)
        logger.info("GitHub repo URL configured: %s", GITHUB_REPO_URL)

        if delete_target:
            try:
                github_link = delete_file_and_push(
                    repo_dir=repo_dir,
                    repo_url=GITHUB_REPO_URL,
                    token=GITHUB_TOKEN,
                    branch=GITHUB_BRANCH,
                    commit_name=GITHUB_COMMIT_NAME,
                    commit_email=GITHUB_COMMIT_EMAIL,
                    file_path=delete_target,
                    logger=logger,
                )
                logger.info("GitHub delete completed: %s", github_link)
            except Exception as exc:
                logger.exception("GitHub delete failed")
                return {"ok": False, "error": f"GitHub delete failed: {exc}"}
        elif not is_delete_request(text):
            if reply and reply != "Ignored":
                try:
                    github_link = create_commit_and_push(
                        repo_dir=repo_dir,
                        repo_url=GITHUB_REPO_URL,
                        token=GITHUB_TOKEN,
                        branch=GITHUB_BRANCH,
                        commit_name=GITHUB_COMMIT_NAME,
                        commit_email=GITHUB_COMMIT_EMAIL,
                        file_path="README.md",
                        content=reply,
                        logger=logger,
                    )
                    logger.info("GitHub update completed: %s", github_link)
                except Exception as exc:
                    logger.exception("GitHub push failed")
                    return {"ok": False, "error": f"GitHub push failed: {exc}"}

    response_text = reply
    if github_link:
        response_text = f"{reply}\n\nGitHub commit: {github_link}"

    try:
        send_reply(logger, chat_id, response_text)
    except Exception as exc:
        logger.exception("Telegram follow-up send failed")
        return {"ok": False, "error": f"Telegram follow-up send failed: {exc}"}

    logger.info("Request completed successfully")
    return {"ok": True, "reply": reply, "github_link": github_link}


@app.get("/health")
def health():
    logger.info("Health check requested")
    return {
        "status": "ok",
        "openai_configured": OPENAI_CONFIGURED,
        "telegram_configured": TELEGRAM_CONFIGURED,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
