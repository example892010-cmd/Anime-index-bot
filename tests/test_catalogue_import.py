from pathlib import Path

import pytest

from app.services.catalogue_service import load_catalogue, seed_catalogue


CATALOGUE = Path(__file__).parents[1] / "data" / "anime.json"


def test_supplied_catalogue_is_valid_and_complete():
    records = load_catalogue(CATALOGUE)
    assert len(records) == 143
    assert len({record["title"] for record in records}) == 143
    assert records[0]["link"].startswith("https://")
    assert records[0]["button_color"] == "green"


@pytest.mark.asyncio
async def test_catalogue_seed_uses_existing_database_and_is_idempotent(db):
    imported = await seed_catalogue(db, CATALOGUE)
    assert imported == 143
    assert (await db.search_anime("black clover"))[0]["title"] == "Black Clover Season 1 & 2"
    links = await db.links_for_anime((await db.search_anime("black clover"))[0]["id"])
    assert links[0]["url"] == "https://t.me/+YAT9u_OFmtswZDVl"
    assert links[0]["quality"] == "480p, 720p, 1080p"
    assert await seed_catalogue(db, CATALOGUE) == 0
    assert (await db.stats())["anime"] == 143
