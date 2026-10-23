# Anime link saving/listing fix

## Root cause found

The uploaded code already had the JSON importer and SQLite link retrieval, and a fresh-database test imports all 143 catalogue records and returns their stored links. The deployed symptom can therefore come from an old database that retained the import-complete marker while containing zero anime records. The uploaded test suite also lacked `tests/conftest.py`, and the browse handler rendered literal `\\n` text instead of line breaks.

## Files changed or added

- `app/handlers/browse.py` — renders real line breaks in the saved catalogue list.
- `bot.py` — logs the imported and saved anime count at startup; existing fail-fast catalogue checks remain unchanged.
- `tests/conftest.py` — restores the temporary SQLite fixture required by the repository tests.
- `LINKS_FIX_README.md` — this explanation.

No database schema, bot token, force-subscription, admin permissions, search logic, or deployment configuration was changed.

## Deployment

Replace the files using the same repository-relative paths, commit, and redeploy. Configure a persistent SQLite path, for example:

```text
DATABASE_PATH=/data/aniflix.db
```

The bot must run with the supplied `data/anime.json` committed at the repository path. The startup log will show `Anime catalogue ready: imported=... saved=...`.

## Verification

After redeploying, search for `Black Clover`, `Attack on Titan`, or `Dragon Ball Complete Series`, and run `/browse`. Confirm the result has the stored Telegram link button. The uploaded repository’s database import test confirms 143 records and the exact stored Black Clover link.
