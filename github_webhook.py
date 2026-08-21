import hashlib
import hmac
import logging
from typing import Any

from config import (
    GITHUB_WEBHOOK_SECRET,
    MOBILE_APP_REPOSITORY,
    QUOTATION_APP_REPOSITORY,
    is_placeholder,
    telegram_admin_chat_ids,
)
from telegram_utils import send_reply


def signature_is_valid(body: bytes, signature: str | None) -> bool:
    """Validate GitHub's X-Hub-Signature-256 header."""
    if is_placeholder(GITHUB_WEBHOOK_SECRET) or not signature:
        return False

    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def app_label(repository: str | None) -> str | None:
    if repository == MOBILE_APP_REPOSITORY:
        return "Font Creator"
    if repository == QUOTATION_APP_REPOSITORY:
        return "Quick Quote"
    return None


def notification_text(event: str, payload: dict[str, Any]) -> str | None:
    """Return a concise Telegram update for supported app-repository events."""
    repository = payload.get("repository", {}).get("full_name")
    label = app_label(repository)
    if label is None:
        return None

    if event == "pull_request":
        action = payload.get("action")
        pull_request = payload.get("pull_request", {})
        number = payload.get("number")
        title = pull_request.get("title", "Untitled pull request")
        url = pull_request.get("html_url")

        if action in {"opened", "reopened", "ready_for_review"}:
            return f"{label} PR #{number} is ready for review:\n{title}\n{url}"

        if action == "closed":
            if pull_request.get("merged"):
                return f"{label} PR #{number} was merged. A build should start shortly.\n{url}"
            return f"{label} PR #{number} was closed without merging.\n{url}"

    if event == "workflow_run" and payload.get("action") == "completed":
        workflow_run = payload.get("workflow_run", {})
        name = workflow_run.get("name", "GitHub Actions workflow")
        conclusion = workflow_run.get("conclusion")
        url = workflow_run.get("html_url")

        if conclusion == "success":
            return f"{label} build passed ({name}).\nDownload the build from Artifacts:\n{url}"
        if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
            return f"{label} build did not complete successfully ({conclusion}).\n{url}"

    return None


def send_notification(logger: logging.Logger, text: str) -> None:
    for chat_id in telegram_admin_chat_ids():
        try:
            send_reply(logger, chat_id, text)
        except Exception:
            logger.exception("Could not send GitHub status update to Telegram chat %s", chat_id)
