import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set in the .env file")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    message_text = Column(Text, nullable=True)


class MobileConfig(Base):
    __tablename__ = "mobile_configs"

    id = Column(Integer, primary_key=True)
    message = Column(Text, nullable=False, default="Welcome")
    enabled = Column(Boolean, nullable=False, default=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
