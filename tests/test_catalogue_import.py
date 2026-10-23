from pathlib import Path

import pytest

from app.database import Database
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
    dragon = (await db.search_anime("dragon ball complete"))[0]
    dragon_link = (await db.links_for_anime(dragon["id"]))[0]
    assert dragon_link["url"] == "https://t.me/Aniflixanimefilestorebot?start=BQADAQADADIxQAAuOS6ERBScfaaJmMIxYE"
    assert await seed_catalogue(db, CATALOGUE) == 0
    assert (await db.stats())["anime"] == 143


def test_empty_or_invalid_catalogue_is_rejected(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_catalogue(empty)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json}", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid catalogue JSON"):
        load_catalogue(invalid)

    with pytest.raises(FileNotFoundError, match="not found"):
        load_catalogue(tmp_path / "missing.json")

    compatible = tmp_path / "without-button-color.json"
    compatible.write_text(
        '[{"title":"Example Anime","link":"https://t.me/example","genre":"Action","audio":"Tamil","quality":["720p"]}]',
        encoding="utf-8",
    )
    assert load_catalogue(compatible)[0]["title"] == "Example Anime"


@pytest.mark.asyncio
async def test_catalogue_survives_database_reopen(tmp_path):
    path = tmp_path / "persistent.db"
    first = await Database(str(path)).connect()
    assert await seed_catalogue(first, CATALOGUE) == 143
    await first.close()

    second = await Database(str(path)).connect()
    assert (await second.search_anime("black clover"))[0]["title"] == "Black Clover Season 1 & 2"
    assert (await second.stats())["anime"] == 143
    assert await seed_catalogue(second, CATALOGUE) == 0
    await second.close()


@pytest.mark.asyncio
async def test_stale_seed_marker_does_not_keep_an_empty_database_empty(db):
    await db.set_setting("catalogue_seeded_v1", "1")
    assert await seed_catalogue(db, CATALOGUE) == 143
    assert (await db.stats())["anime"] == 143
