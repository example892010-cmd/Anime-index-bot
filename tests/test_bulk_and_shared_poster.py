import pytest

from app.handlers.admin_panel import parse_bulk_anime_input
from app.keyboards import admin_keyboard, manage_posts_keyboard
from app.services.anime_service import AnimeService
from app.utils import normalize_title


@pytest.mark.asyncio
async def test_bulk_entries_create_separate_searchable_records(db):
    entries, invalid = parse_bulk_anime_input(
        "🍥 Naruto - https://example.com/naruto - green - Naruto post\n"
        "⚔️ Solo Leveling - https://example.com/solo - blue - Solo post\n"
        "🔥 Demon Slayer - https://example.com/demon - red - Demon post"
    )
    assert invalid == []
    for title, url, color, post in entries:
        anime_id = await AnimeService(db).create(title, description=post)
        await db.add_link(anime_id, title, "external", url, button_color=color)
    assert (await db.stats())["anime"] == 3
    for title, url, color, post in entries:
        row = (await db.search_anime(normalize_title(title)))[0]
        assert row["description"] == post
        link = (await db.links_for_anime(row["id"]))[0]
        assert link["url"] == url
        assert link["button_color"] == color


def test_admin_has_one_manage_posts_button():
    buttons = [button for row in admin_keyboard().inline_keyboard for button in row if button.text == "📝 Manage Posts"]
    assert len(buttons) == 1
    assert len(manage_posts_keyboard().inline_keyboard) == 4


@pytest.mark.asyncio
async def test_shared_poster_setting_is_persistent(db):
    await db.set_setting("shared_landscape_poster", "telegram-file-id")
    assert await db.get_setting("shared_landscape_poster") == "telegram-file-id"
