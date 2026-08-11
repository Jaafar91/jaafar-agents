import os
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from database import init_db, get_db, Message

load_dotenv()

PORT = int(os.getenv("PORT", "8000"))
app = FastAPI(title="Telegram Webhook Receiver")


@app.on_event("startup")
def on_startup():
    init_db()


# Telegram sends nested JSON; we only need the text from message
class TelegramMessage(BaseModel):
    message_id: int
    text: Optional[str] = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None


@app.post("/webhook")
def telegram_webhook(update: TelegramUpdate, db: Session = Depends(get_db)):
    text = update.message.text if update.message else None

    record = Message(message_text=text)
    db.add(record)
    db.commit()
    db.refresh(record)

    return {"ok": True, "id": record.id}


@app.get("/messages")
def list_messages(db: Session = Depends(get_db)):
    return db.query(Message).all()


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
