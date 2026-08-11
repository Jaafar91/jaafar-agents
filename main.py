import logging
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telegram_openai_bot")

load_dotenv()

PORT = int(os.getenv("PORT", "9000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_COMMIT_NAME = os.getenv("GITHUB_COMMIT_NAME", "OpenAI Bot")
GITHUB_COMMIT_EMAIL = os.getenv("GITHUB_COMMIT_EMAIL", "bot@example.com")

def _is_placeholder(value: Optional[str]) -> bool:
    if not value:
        return True
    value = value.strip()
    return value.startswith("your_") or value.endswith("_here") or value in {"", "example"}


OPENAI_CONFIGURED = not _is_placeholder(OPENAI_API_KEY)
TELEGRAM_CONFIGURED = not _is_placeholder(TELEGRAM_BOT_TOKEN) and not _is_placeholder(TELEGRAM_CHAT_ID)

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_CONFIGURED else None
app = FastAPI(title="Telegram Webhook Receiver")


# Telegram sends nested JSON; we only need the text from message
class TelegramMessage(BaseModel):
    message_id: int
    text: Optional[str] = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def root():
    return {"status": "ok"}


def _run_git_command(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _build_repo_remote_url(repo_url: str, token: Optional[str] = None) -> str:
    if not token or not repo_url.startswith("https://"):
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.netloc.startswith("github.com"):
        netloc = f"x-access-token:{token}@github.com"
    else:
        netloc = f"x-access-token:{token}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


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
        logger.info("Calling OpenAI with model %s", OPENAI_MODEL)
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant for a Telegram bot that can also help manage a GitHub repository.",
                },
                {"role": "user", "content": text},
            ],
        )
        logger.info("OpenAI response received")
    except Exception as exc:
        logger.exception("OpenAI request failed")
        return {"ok": False, "error": f"OpenAI request failed: {exc}"}

    reply = response.output_text
    logger.info("OpenAI reply: %s", reply)

    if TELEGRAM_CONFIGURED:
        try:
            logger.info("Sending reply to Telegram chat %s", TELEGRAM_CHAT_ID)
            response_tg = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": reply},
                timeout=10,
            )
            logger.info("Telegram API response: %s %s", response_tg.status_code, response_tg.text)
        except Exception as exc:
            logger.exception("Telegram send failed")
            return {"ok": False, "error": f"Telegram send failed: {exc}"}
    else:
        logger.error("Telegram bot credentials are not configured")
        return {"ok": False, "error": "Telegram bot credentials are not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the .env file."}

    if GITHUB_REPO_URL:
        repo_dir = os.path.join(os.getcwd(), "repo")
        os.makedirs(repo_dir, exist_ok=True)
        remote_url = _build_repo_remote_url(GITHUB_REPO_URL, GITHUB_TOKEN)
        logger.info("GitHub repo URL configured: %s", GITHUB_REPO_URL)

        if not os.path.exists(os.path.join(repo_dir, ".git")):
            logger.info("Cloning repository into %s", repo_dir)
            _run_git_command(["git", "clone", remote_url, repo_dir], os.getcwd())
        else:
            logger.info("Updating remote URL for repository in %s", repo_dir)
            _run_git_command(["git", "remote", "set-url", "origin", remote_url], repo_dir)

        try:
            logger.info("Checking out branch %s", GITHUB_BRANCH)
            _run_git_command(["git", "checkout", GITHUB_BRANCH], repo_dir)
        except RuntimeError:
            logger.warning("Branch %s does not exist, creating it", GITHUB_BRANCH)
            _run_git_command(["git", "checkout", "-b", GITHUB_BRANCH], repo_dir)

        with open(os.path.join(repo_dir, "README.md"), "a", encoding="utf-8") as f:
            f.write(f"\n<!-- Auto-generated by OpenAI bot at {os.getenv('PORT', '9000')} -->\n")

        _run_git_command(["git", "config", "user.name", GITHUB_COMMIT_NAME], repo_dir)
        _run_git_command(["git", "config", "user.email", GITHUB_COMMIT_EMAIL], repo_dir)
        _run_git_command(["git", "add", "README.md"], repo_dir)
        _run_git_command(["git", "commit", "-m", "Update from OpenAI bot"], repo_dir)

        try:
            logger.info("Pushing to GitHub branch %s", GITHUB_BRANCH)
            _run_git_command(["git", "push", "origin", GITHUB_BRANCH], repo_dir)
        except RuntimeError as exc:
            logger.exception("GitHub push failed")
            return {"ok": False, "error": f"GitHub push failed: {exc}"}

    logger.info("Request completed successfully")
    return {"ok": True, "reply": reply}


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
