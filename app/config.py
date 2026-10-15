from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    database_path: str = "data/aniflix.db"
    bot_name: str = "ANIFLIX NETWORK TAMIL"
    start_image: str = ""
    updates_channel: str = ""
    support_link: str = ""
    log_level: str = "INFO"
    timezone: str = "Asia/Kolkata"

    @property
    def database_parent(self) -> Path:
        return Path(self.database_path).expanduser().resolve().parent


def load_config(*, require_token: bool = True) -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    owner_raw = os.getenv("OWNER_ID", "").strip()
    if require_token and not token:
        raise ValueError("BOT_TOKEN is required")
    if not owner_raw:
        raise ValueError("OWNER_ID is required")
    try:
        owner_id = int(owner_raw)
    except ValueError as exc:
        raise ValueError("OWNER_ID must be a numeric Telegram user ID") from exc
    if owner_id <= 0:
        raise ValueError("OWNER_ID must be positive")
    return Settings(
        bot_token=token,
        owner_id=owner_id,
        database_path=os.getenv("DATABASE_PATH", "data/aniflix.db").strip() or "data/aniflix.db",
        bot_name=os.getenv("BOT_NAME", "ANIFLIX NETWORK TAMIL").strip() or "ANIFLIX NETWORK TAMIL",
        start_image=os.getenv("START_IMAGE", "").strip(),
        updates_channel=os.getenv("UPDATES_CHANNEL", "").strip(),
        support_link=os.getenv("SUPPORT_LINK", "").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timezone=os.getenv("TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata",
    )
