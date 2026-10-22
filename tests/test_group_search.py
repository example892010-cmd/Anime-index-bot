import pytest

from app.handlers.search import _group_match_list
from app.services.search_service import SearchService


@pytest.mark.asyncio
async def test_group_search_uses_existing_records_and_aliases(db):
    naruto = await db.create_anime("Naruto", "naruto")
    await db.create_anime("One Piece", "one piece")
    await db.add_alias(naruto, "Ninja Naruto", "ninja naruto")

    assert (await SearchService(db).search("NARUTO"))[0]["id"] == naruto
    assert (await SearchService(db).search("one piece"))[0]["title"] == "One Piece"
    assert (await SearchService(db).search("ninja naruto"))[0]["id"] == naruto
    assert await SearchService(db).search("unknown anime") == []


def test_group_broad_query_is_a_short_list_not_full_posts():
    rows = [{"title": "Naruto"}, {"title": "Naruto Shippuden"}, {"title": "Boruto"}]

    text = _group_match_list(rows)

    assert text == "🔎 Matching anime:\n\n1. Naruto\n2. Naruto Shippuden\n3. Boruto"
