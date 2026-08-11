import os
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PORT = int(os.getenv("PORT", "9000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY must be set in the .env file")

client = OpenAI(api_key=OPENAI_API_KEY)
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


@app.post("/telegram/webhook")
def telegram_webhook(update: TelegramUpdate):
    text = update.message.text if update.message else None

    if not text:
        return {"ok": False, "error": "No message text provided"}

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "system",
                "content": "You are a helpful assistant for a Telegram bot.",
            },
            {"role": "user", "content": text},
        ],
    )

    reply = response.output_text

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        import requests

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": reply},
            timeout=10,
        )

    return {"ok": True, "reply": reply}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
