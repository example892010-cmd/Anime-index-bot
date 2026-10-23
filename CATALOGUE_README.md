# Catalogue integration

## Files added or modified

- `data/anime.json` — the supplied 143-record catalogue, preserved as provided.
- `app/services/catalogue_service.py` — validates and imports the catalogue into the existing SQLite database.
- `bot.py` — runs the one-time import during startup when `data/anime.json` is present.
- `app/handlers/search.py` — displays catalogue genre, Tamil audio, and quality metadata while retaining existing result buttons.
- `tests/test_catalogue_import.py` — validates the catalogue and import behavior.

## Storage and persistence

`data/anime.json` is the initial catalogue source. On the first startup, missing records are inserted into the existing SQLite database and the `catalogue_seeded_v1` setting prevents duplicate re-imports. Existing database records are never deleted or reset. Configure `DATABASE_PATH` on a persistent Render disk or Railway volume so imported records and later admin changes survive redeployments.

Startup now fails clearly if `data/anime.json` is missing, invalid JSON, or an empty array. The bot never replaces a missing or invalid catalogue with `[]` and never marks an empty catalogue as successfully imported.

The supplied `button_color` values remain unchanged in the JSON file. Telegram inline buttons do not expose a custom color API, so the existing inline button implementation cannot render a literal green button; the catalogue link itself is preserved exactly.

## Upload and deploy

Upload the files using their repository paths, especially `data/anime.json` and `app/services/catalogue_service.py`. Commit and deploy normally. No new environment variables are required.

Admin changes are stored in SQLite. This integration does not write changes back to GitHub because no GitHub token/repository credentials were provided and it would be unsafe to invent them. If GitHub-backed JSON editing is required, configure a separate audited GitHub Contents API integration with `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, and `GITHUB_BRANCH` before enabling it.
