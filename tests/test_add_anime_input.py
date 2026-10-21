from app.handlers.admin_panel import ONGOING_ANIMES_URL, parse_add_anime_input


def test_add_anime_parser_preserves_post_text_exactly():
    post = "Support Group: https://t.me/example\n🔥 Episode 1\n✨ Tamil audio"
    value = f"🍥 Naruto - https://example.com/naruto - green - {post}"

    title, url, color, parsed_post = parse_add_anime_input(value)

    assert title == "🍥 Naruto"
    assert url == "https://example.com/naruto"
    assert color == "green"
    assert parsed_post == post


def test_add_anime_parser_rejects_malformed_input():
    try:
        parse_add_anime_input("Naruto - https://example.com - green")
    except ValueError as exc:
        assert "Use:" in str(exc)
    else:
        raise AssertionError("Malformed Add Anime input should be rejected")


def test_ongoing_button_url_is_fixed():
    assert ONGOING_ANIMES_URL == "https://t.me/+cVwTJSgsEvUzNWZl"
