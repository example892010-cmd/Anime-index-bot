from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.database import Database
from app.utils import normalize_title, valid_url

CATALOGUE_SEEDED_SETTING = "catalogue_seeded_v1"
REQUIRED_FIELDS = {"title", "link", "genre", "audio", "quality", "button_color"}


def load_catalogue(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise ValueError("Catalogue must be a JSON array")

    validated: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for record in records:
        if not isinstance(record, dict) or not REQUIRED_FIELDS.issubset(record):
            raise ValueError("Each catalogue record must contain all required fields")
        title = record["title"]
        link = record["link"]
        qualities = record["quality"]
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Catalogue titles must be non-empty strings")
        if not isinstance(link, str) or not valid_url(link):
            raise ValueError(f"Invalid link for catalogue title: {title}")
        if not isinstance(qualities, list) or not all(isinstance(item, str) for item in qualities):
            raise ValueError(f"Quality must be a list of strings for: {title}")
        key = (title, link, record["genre"], record["audio"], tuple(qualities), record["button_color"])
        if key in seen:
            continue
        seen.add(key)
        validated.append(record)
    return validated


async def seed_catalogue(db: Database, path: str | Path) -> int:
    """Import the initial JSON catalogue once, preserving existing DB records."""
    if await db.get_setting(CATALOGUE_SEEDED_SETTING, "0") == "1":
        return 0

    records = load_catalogue(path)
    imported = 0
    for record in records:
        normalized = normalize_title(record["title"])
        existing = await db.search_anime(normalized, limit=10)
        if any(row["normalized_title"] == normalized for row in existing):
            continue
        anime_id = await db.create_anime(
            record["title"],
            normalized,
            audio_language=record["audio"],
            genres=record["genre"],
        )
        await db.add_link(
            anime_id,
            f"🟢 {record['title']}",
            "external",
            record["link"],
            quality=", ".join(record["quality"]),
            language=record["audio"],
            display_order=0,
        )
        imported += 1
    await db.set_setting(CATALOGUE_SEEDED_SETTING, "1")
    return imported
