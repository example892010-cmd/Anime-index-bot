# ANIFLIX Anime Finder Bot

A GitHub-ready Telegram anime search bot for **ANIFLIX NETWORK TAMIL**, built with Python 3.11+, aiogram 3, aiosqlite, and SQLite. This repository is an honest working MVP: the core catalog search, alias matching, persistent tracking, permissions, personalized `/start`, and database schema are implemented; advanced workflows are clearly identified below.

## Features

- Long-polling aiogram 3 bot with graceful database/session shutdown.
- SQLite schema initialized automatically with foreign keys, indexes, settings, audit logs, aliases, links, force-subscription records, users, and search logs.
- Unicode/case/whitespace/punctuation-normalized title search with exact-title priority, aliases, prefix/partial matching, and conservative suggestions.
- Poster URL or Telegram file ID support, descriptions, type, audio, metadata, and actual stored HTTP(S) link buttons.
- Search tracking records every submitted anime query exactly once, including failed searches. `/start`, help, admin commands, and membership checks do not count as searches.
- Personalized, configurable welcome message using `{first_name}`, optional image, and configurable updates link.
- Owner/admin authorization enforced server-side for `/admin`, `/users`, statistics, callbacks, admin management, backups, and force-subscription changes.
- Dynamic force-subscription records in SQLite, with public/private channel fields, global enable/disable, dynamic join buttons, and real `get_chat_member` verification when Telegram access permits.
- SQLite online backup creation, integrity/schema validation, and protected restore service.
- Docker, Render worker, and Railway deployment descriptors.

## Repository structure

```text
bot.py                 # long-polling entry point
config.py              # environment validation
app/database.py        # schema, migrations, queries, transactions
app/services/          # search, anime, links, force-sub, user, backup services
app/handlers/          # start/search/admin/user/force-sub routers
app/keyboards.py       # public/admin/force-sub inline keyboards
tests/                 # async SQLite and service tests
Dockerfile
render.yaml
railway.json
```

## Local setup

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, then:
python bot.py
```

`DATABASE_PATH=data/aniflix.db` is suitable for local development. In production use `/data/aniflix.db` on a persistent disk/volume. Do not run two long-polling instances using one token/database.

### Required configuration

| Variable | Required | Description |
|---|---:|---|
| `BOT_TOKEN` | yes | BotFather token; never commit it |
| `OWNER_ID` | yes | Numeric Telegram user ID of the immutable owner |
| `DATABASE_PATH` | no | Defaults to `data/aniflix.db`; use `/data/aniflix.db` in production |
| `BOT_NAME` | no | Display name retained by runtime config |
| `START_IMAGE` | no | Optional initial image URL/file ID |
| `UPDATES_CHANNEL` | no | Optional `http(s)` URL used by the welcome keyboard |
| `SUPPORT_LINK` | no | Optional support link |
| `LOG_LEVEL` | no | Defaults to `INFO` |
| `TIMEZONE` | no | Defaults to `Asia/Kolkata` |

The application fails fast if `OWNER_ID` is missing or non-numeric. `BOT_TOKEN` is required by `bot.py` but can be omitted in configuration-only tests.

## BotFather and owner setup

1. Create a bot with BotFather and copy the token into `.env`.
2. Obtain the owner ID from a trusted Telegram ID utility or a temporary diagnostic bot; never use a username as the owner key.
3. Set `OWNER_ID` to that numeric ID.
4. Start the bot and send `/admin` from the owner account.
5. Use `/addadmin TELEGRAM_USER_ID` and `/removeadmin TELEGRAM_USER_ID`. The configured owner cannot be removed by these workflows.

## Using the bot

- `/start` shows the personalized welcome and buttons.
- Send any ordinary text containing an anime title to search.
- `/browse` lists up to 50 active titles.
- `/admin` opens the protected inline panel.
- `/users`, `/userstats`, and `/recentsearches` are protected and show tracked search activity only.
- `/cancel` clears the active admin FSM workflow.

### Add anime and links

From `/admin`, select **Add Anime**, enter a title, then enter comma-separated aliases or `skip`. Duplicate normalized titles and duplicate aliases are rejected. A created title displays its numeric ID.

The service layer validates and stores actual HTTP(S) links; it never fabricates Telegram URLs. For a quick admin link insertion use:

```text
/addlink ANIME_ID DISPLAY_TITLE URL
```

The current handler set includes the validated `LinkService` and stored link buttons. Advanced guided edit/reorder/delete screens are not included in this MVP and are called out as limitations rather than pretending to be complete.

### Force subscription

Force-subscription configuration is database-backed and applies to subsequent checks without redeployment:

```text
/forcesub_add CHANNEL_ID DISPLAY_NAME [INVITE_URL]
/forcesub_remove CHANNEL_ID
/forcesub_on
/forcesub_off
/checkchannels
```

For a public channel, supply its numeric channel ID and optionally a username through a future guided admin workflow; for a private channel, supply the numeric ID and the private invite URL separately. The bot must be able to access the channel, typically as an administrator, for reliable membership checks. Inaccessible channels are treated as not verified and reported rather than silently accepted.

The global switch is automatically enabled when a channel is added. `/checkchannels` reports access failures and recommends adding the bot with suitable channel permissions.

### Users and statistics

Only the owner and rows in `admins` can use `/users`, `/userstats`, and `/recentsearches`. A user enters the searching-user list only after submitting a query. Repeated searches update one aggregate user row and append a search log. Missing usernames are displayed safely as `—` in the stored data model.

### Backups

The admin panel's **Database Backup** action creates a consistent SQLite backup using SQLite's backup API and sends it as a document. The `BackupService` validates integrity plus required tables before restore. Always keep an independent copy of the database volume. Restore upload/confirmation UI is not yet exposed as a Telegram document workflow; the service is ready for a protected upload handler and the limitation is documented honestly.

## Deployment

### Render

1. Push this repository to GitHub and create a Render **Background Worker** from it.
2. Render reads `render.yaml` and builds the included Dockerfile.
3. Attach the persistent disk declared in `render.yaml` at `/data` (at least 1 GB).
4. Set `BOT_TOKEN` and `OWNER_ID` as secret environment variables. Confirm `DATABASE_PATH=/data/aniflix.db`.
5. Deploy exactly one worker instance for the token.

### Railway

1. Create a Railway project from the repository; it uses the root `Dockerfile` and `railway.json`.
2. Add a persistent volume mounted at `/data`.
3. Set `BOT_TOKEN`, `OWNER_ID`, and `DATABASE_PATH=/data/aniflix.db` in Railway variables.
4. Deploy one replica and verify logs show successful polling/database initialization.

Using the default local path in production without a volume will lose data on redeploy; this is intentionally not silently hidden.

## Testing and verification

Run:

```bash
python3 -m compileall -q .
pytest -q
```

The included tests use temporary SQLite files and do not require a live Telegram token or channels. They cover schema persistence, duplicate prevention, normalization, alias/exact/partial search, conservative no-match behavior, tracking, owner/admin permissions, force-sub persistence, URL validation, configuration parsing, and backup integrity.

## Current MVP limitations

- The inline admin panel renders every requested category and protects callbacks. The Add Anime, welcome, start-image, statistics, admin, force-sub, and backup actions are functional. Detailed guided screens for poster/link/alias editing, link reorder/delete/enable, post management, search-user-by-ID, restore upload confirmation, and multi-page inline navigation are not implemented yet; those buttons return an explicit limitation message rather than claiming success.
- `/addlink` is documented and should be wired into a guided admin workflow before broad production use; the underlying validated service and database table are implemented.
- Search suggestions are conservative database partial matches, not an external fuzzy-search engine.
- Telegram membership verification depends on real bot channel access and Telegram API responses; no live channel verification is claimed by local tests.
- No mass messaging is implemented by design.

No MongoDB, PostgreSQL, external search API, real token, or private link is included.

## License

MIT.
