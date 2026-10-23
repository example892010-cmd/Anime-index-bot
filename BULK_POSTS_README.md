# Bulk Add Anime and shared Manage Posts update

## Changed files

- `app/handlers/admin_panel.py` — bulk line parser, per-entry validation, duplicate/save-failure reporting, and shared poster workflows.
- `app/keyboards.py` — functional Manage Posts submenu and removal confirmation keyboard.
- `app/states.py` — bulk Add Anime and shared-poster FSM states.
- `app/database.py` — safe `anime_links.button_color` migration and persistence.
- `app/services/link_service.py` — supported-color validation.
- `app/handlers/search.py` — shared landscape poster for all anime results with text fallback.
- `tests/test_add_anime_input.py` and `tests/test_bulk_and_shared_poster.py` — coverage for bulk parsing, records, colors, duplicates, menu, and settings.
- `tests/conftest.py` — SQLite test fixture required by the repository tests.

## Usage

Open `/admin`, choose `➕ Add Anime`, and send one entry per line:

```text
🍥 Naruto - https://example.com/naruto - green - Naruto post text
⚔️ Solo Leveling - https://example.com/solo - blue (Solo Leveling post text)
🔥 Demon Slayer - https://example.com/demon-slayer - red - Demon Slayer post text
```

Blank lines are ignored. Valid lines are saved independently. Invalid lines, duplicates, and save failures are reported with counts and line details. Existing records are not overwritten.

Choose `📝 Manage Posts` to set, preview, or remove one shared landscape Telegram image. The image is stored as a Telegram file ID in `bot_settings` and is reused for every search result. Individual anime posters remain separate. If the shared image is unavailable, search falls back to text.

The SQLite schema migration is non-destructive. Keep the existing persistent `DATABASE_PATH` (Render example: `/data/aniflix.db`) so new anime and poster settings survive restarts. Telegram inline buttons do not support custom colors; the requested color is validated and stored per link as metadata, while the rendered button uses Telegram’s standard appearance.
