from sqlalchemy.orm import Session
from database import MobileConfig


def read_config(db: Session) -> MobileConfig:
    config = db.get(MobileConfig, 1)
    if config is None:
        config = MobileConfig(id=1, message="Welcome", enabled=True)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def set_message(db: Session, message: str) -> MobileConfig:
    config = read_config(db)
    config.message = message.strip()
    db.commit()
    db.refresh(config)
    return config


def set_enabled(db: Session, enabled: bool) -> MobileConfig:
    config = read_config(db)
    config.enabled = enabled
    db.commit()
    db.refresh(config)
    return config
