from app.handlers.admin_panel import ONGOING_ANIMES_URL, parse_bulk_anime_input


def test_bulk_parser_preserves_each_entry_and_parentheses():
    source = (
        "🍥 Naruto - https://example.com/naruto - green - (Support Group) Naruto\n"
        "\n"
        "⚔️ Solo Leveling - https://example.com/solo - blue (Solo (Leveling) post)\n"
        "🔥 Demon Slayer - https://example.com/demon - red - Episodes"
    )
    entries, invalid = parse_bulk_anime_input(source)
    assert invalid == []
    assert entries == [
        ("🍥 Naruto", "https://example.com/naruto", "green", "(Support Group) Naruto"),
        ("⚔️ Solo Leveling", "https://example.com/solo", "blue", "(Solo (Leveling) post)"),
        ("🔥 Demon Slayer", "https://example.com/demon", "red", "Episodes"),
    ]


def test_bulk_parser_reports_invalid_lines_without_discarding_valid_lines():
    entries, invalid = parse_bulk_anime_input(
        "Good - https://example.com/good - green - post\n"
        "Bad - not-a-url - purple - post\n"
        "Also Good - https://example.com/also - red - post"
    )
    assert [entry[0] for entry in entries] == ["Good", "Also Good"]
    assert invalid == [(2, "invalid title, URL, color, or post text")]


def test_ongoing_button_url_is_fixed():
    assert ONGOING_ANIMES_URL == "https://t.me/+cVwTJSgsEvUzNWZl"
