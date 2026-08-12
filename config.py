import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "9000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_ADMIN_CHAT_IDS = os.getenv("TELEGRAM_ADMIN_CHAT_IDS", TELEGRAM_CHAT_ID or "")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_COMMIT_NAME = os.getenv("GITHUB_COMMIT_NAME", "OpenAI Bot")
GITHUB_COMMIT_EMAIL = os.getenv("GITHUB_COMMIT_EMAIL", "bot@example.com")


def is_placeholder(value):
    if not value:
        return True
    value = value.strip()
    return value.startswith("your_") or value.endswith("_here") or value in {"", "example"}


def telegram_admin_chat_ids():
    return {item.strip() for item in TELEGRAM_ADMIN_CHAT_IDS.split(",") if item.strip()}
