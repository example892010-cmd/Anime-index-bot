import os
import json
import logging
import difflib
from pathlib import Path
from urllib.parse import urlparse

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"

# Use a persistent disk on Render if you want data to survive redeploys.
DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "anime_db.txt"
SETTINGS_FILE = DATA_DIR / "anime_settings.json"

MAX_BATCH_ENTRIES = 50

DEFAULT_POST_TEXT = (
    "🍥 {anime}\n\n"
    "🎧 Audio: Tamil Audio\n"
    "📺 Quality: 480p | 720p | 1080p\n\n"
    "👇 Click the below button to download!"
)

WELCOME_TEXT = (
    "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
    "Just type the anime name you want to find! 🔎\n\n"
    "Use /list to get the anime list.\n\n"
    "Powered By: @ANIFLIXANIMETAMIL"
)

NO_ANIME_TEXT = (
    "❌ No Anime Found!\n\n"
    "Anime name correct-ah type pannunga or spelling check pannunga. 🔎\n"
    "If the anime is missing, please tell us in the support group.\n\n"
    "Use /list to get the available anime list."
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ================= DATA =================

def load_anime():
    if not DB_FILE.exists():
        DB_FILE.write_text("", encoding="utf-8")

    anime_list = []

    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split(" - ", 2)]

        if len(parts) < 2:
            continue

        title, url = parts[0], parts[1]
        search_name = parts[2] if len(parts) > 2 else title

        if title and valid_url(url):
            anime_list.append({
                "title": title,
                "url": url,
                "search_name": search_name,
            })

    return anime_list


def save_anime(anime_list):
    lines = [
        f"{item['title']} - {item['url']} - {item['search_name']}"
        for item in anime_list
    ]
    DB_FILE.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def load_settings():
    if not SETTINGS_FILE.exists():
        return {
            "expiry_minutes": 10,
            "post_text": DEFAULT_POST_TEXT,
            "global_poster": None,
            "posters": {},
        }

    try:
        settings = json.loads(
            SETTINGS_FILE.read_text(encoding="utf-8")
        )
        settings.setdefault("expiry_minutes", 10)
        settings.setdefault("post_text", DEFAULT_POST_TEXT)
        settings.setdefault("global_poster", None)
        settings.setdefault("posters", {})
        return settings
    except (json.JSONDecodeError, OSError):
        return {
            "expiry_minutes": 10,
            "post_text": DEFAULT_POST_TEXT,
            "global_poster": None,
            "posters": {},
        }


def save_settings(settings):
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def valid_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def normalize(text):
    return " ".join(text.casefold().strip().split())


def find_anime(query, anime_list):
    query_normalized = normalize(query)

    if not query_normalized:
        return None

    # Exact title or search-name match.
    for anime in anime_list:
        if query_normalized in (
            normalize(anime["title"]),
            normalize(anime["search_name"]),
        ):
            return anime

    # Partial match.
    for anime in anime_list:
        title = normalize(anime["title"])
        search_name = normalize(anime["search_name"])

        if (
            query_normalized in title
            or title in query_normalized
            or query_normalized in search_name
            or search_name in query_normalized
        ):
            return anime

    # Fuzzy spelling match.
    choices = []
    for anime in anime_list:
        choices.extend([
            (normalize(anime["title"]), anime),
            (normalize(anime["search_name"]), anime),
        ])

    matches = difflib.get_close_matches(
        query_normalized,
        [name for name, _ in choices],
        n=1,
        cutoff=0.72,
    )

    if matches:
        for name, anime in choices:
            if name == matches[0]:
                return anime

    return None


# ================= KEYBOARDS =================

def welcome_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "ONGOING ANIME",
                url="https://t.me/+cVwTJSgsEvUzNWZl",
            )
        ],
        [
            InlineKeyboardButton(
                "SUPPORT GROUP",
                url="https://t.me/+4YfcdPoSdK9jYmVl",
            )
        ],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Anime", callback_data="admin_add"),
            InlineKeyboardButton("➖ Remove Anime", callback_data="admin_remove"),
        ],
        [
            InlineKeyboardButton("📄 Import TXT", callback_data="admin_import"),
            InlineKeyboardButton("🖼 Poster Settings", callback_data="admin_poster"),
        ],
        [
            InlineKeyboardButton("✏️ Post Text", callback_data="admin_post_text"),
            InlineKeyboardButton("📋 View List", callback_data="admin_list"),
        ],
        [
            InlineKeyboardButton("⏱ Set Expiry", callback_data="admin_expiry"),
            InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel"),
        ],
    ])


# ================= HELPERS =================

def is_admin(user_id):
    return user_id in ADMIN_IDS


async def send_anime_result(message, anime, settings):
    post_text = settings.get("post_text", DEFAULT_POST_TEXT)

    try:
        text = post_text.format(
            anime=anime["title"],
            link=anime["url"],
            bot_title=BOT_TITLE,
            expiry=settings.get("expiry_minutes", 10),
        )
    except (KeyError, ValueError):
        text = DEFAULT_POST_TEXT.format(
            anime=anime["title"],
            link=anime["url"],
            bot_title=BOT_TITLE,
            expiry=settings.get("expiry_minutes", 10),
        )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 DOWNLOAD NOW", url=anime["url"])
    ]])

    poster = settings.get("posters", {}).get(
        normalize(anime["title"])
    ) or settings.get("global_poster")

    if poster:
        try:
            await message.reply_photo(
                photo=poster,
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Could not send poster; sending text instead.")

    await message.reply_text(text, reply_markup=keyboard)


async def send_no_anime(message, query, anime_list):
    """Always reply when a non-command search has no match."""
    suggestions = []

    if anime_list and query.strip():
        names = [item["title"] for item in anime_list]
        suggestions = difflib.get_close_matches(
            query.strip(),
            names,
            n=3,
            cutoff=0.35,
        )

    text = NO_ANIME_TEXT

    if suggestions:
        text += "\n\n💡 Did you mean:\n"
        text += "\n".join(f"• {name}" for name in suggestions)

    await message.reply_text(text)


# ================= USER COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        WELCOME_TEXT,
        reply_markup=welcome_keyboard(),
    )


async def list_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    anime_list = load_anime()

    if not anime_list:
        await update.effective_message.reply_text(
            "📂 Anime list is currently empty."
        )
        return

    # Avoid exceeding Telegram's message length limit.
    lines = ["📚 ANIFLIX ANIME LIST\n"]
    for index, anime in enumerate(anime_list, start=1):
        lines.append(f"{index}. {anime['title']}")

    full_text = "\n".join(lines)

    for start_index in range(0, len(full_text), 3500):
        await update.effective_message.reply_text(
            full_text[start_index:start_index + 3500]
        )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ You are not authorized.")
        return

    context.user_data.pop("admin_action", None)
    await update.effective_message.reply_text(
        "⚙️ ANIFLIX ADMIN PANEL",
        reply_markup=admin_keyboard(),
    )


# ================= ADMIN CALLBACKS =================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if not is_admin(user.id):
        await query.message.reply_text("⛔ You are not authorized.")
        return

    action = query.data

    if action == "admin_cancel":
        context.user_data.pop("admin_action", None)
        await query.message.reply_text("✅ Cancelled.")
        return

    actions = {
        "admin_add": (
            "add",
            "Send anime entries like:\n"
            "`NARUTO - https://example.com/naruto - naruto`\n\n"
            "You can send multiple lines, up to 50 entries.",
        ),
        "admin_remove": (
            "remove",
            "Send the anime title or its download URL to remove it.",
        ),
        "admin_import": (
            "import",
            "Upload a `.txt` file with one entry per line:\n"
            "`TITLE - URL - SEARCH NAME`",
        ),
        "admin_poster": (
            "poster_choice",
            "Send `global` to set a shared poster, or `anime` to set a poster for one anime.",
        ),
        "admin_post_text": (
            "post_text",
            "Send the new post template.\n\n"
            "Available placeholders: `{anime}`, `{link}`, `{bot_title}`, `{expiry}`\n"
            "Send `reset default` to restore the default template.",
        ),
        "admin_expiry": (
            "expiry",
            "Send the expiry time in minutes (number only).",
        ),
        "admin_list": (
            None,
            "📚 Current anime list:\n" + (
                "\n".join(a["title"] for a in load_anime()) or "No anime added."
            ),
        ),
    }

    if action not in actions:
        return

    next_action, response = actions[action]

    if next_action:
        context.user_data["admin_action"] = next_action

    await query.message.reply_text(response)


# ================= ADMIN TEXT ACTIONS =================

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not is_admin(user.id):
        return

    action = context.user_data.get("admin_action")
    if not action:
        return

    text = (message.text or "").strip()

    if action == "add":
        anime_list = load_anime()
        added, skipped = [], []

        for line in text.splitlines()[:MAX_BATCH_ENTRIES]:
            parts = [p.strip() for p in line.split(" - ", 2)]

            if len(parts) < 2:
                skipped.append(line)
                continue

            title, url = parts[0], parts[1]
            search_name = parts[2] if len(parts) > 2 else title

            if not title or not valid_url(url):
                skipped.append(line)
                continue

            exists = any(
                normalize(a["title"]) == normalize(title)
                or normalize(a["url"]) == normalize(url)
                for a in anime_list
            )

            if exists:
                skipped.append(title + " (duplicate)")
                continue

            anime_list.append({
                "title": title,
                "url": url,
                "search_name": search_name,
            })
            added.append(title)

        save_anime(anime_list)
        context.user_data.pop("admin_action", None)

        result = f"✅ Added: {len(added)}\n⏭ Skipped: {len(skipped)}"
        if added:
            result += "\n\n" + "\n".join(added[:20])
        if skipped:
            result += "\n\nSkipped entries:\n" + "\n".join(skipped[:10])

        await message.reply_text(result)
        return

    if action == "remove":
        anime_list = load_anime()
        before = len(anime_list)

        anime_list = [
            a for a in anime_list
            if normalize(a["title"]) != normalize(text)
            and normalize(a["url"]) != normalize(text)
            and normalize(a["search_name"]) != normalize(text)
        ]

        save_anime(anime_list)
        context.user_data.pop("admin_action", None)

        if len(anime_list) < before:
            await message.reply_text("✅ Anime removed.")
        else:
            await message.reply_text("❌ Anime not found in the database.")
        return

    if action == "poster_choice":
        if text.casefold() == "global":
            context.user_data["admin_action"] = "global_poster"
            await message.reply_text("Now send the photo for the shared poster.")
        elif text.casefold() == "anime":
            context.user_data["admin_action"] = "anime_poster_name"
            await message.reply_text("Send the exact anime title first.")
        else:
            await message.reply_text("Send `global` or `anime`.")
        return

    if action == "anime_poster_name":
        context.user_data["poster_anime_name"] = text
        context.user_data["admin_action"] = "anime_poster_photo"
        await message.reply_text("Now send the poster photo.")
        return

    if action == "post_text":
        settings = load_settings()

        if text.casefold() == "reset default":
            settings["post_text"] = DEFAULT_POST_TEXT
            save_settings(settings)
            context.user_data.pop("admin_action", None)
            await message.reply_text("✅ Default post text restored.")
            return

        if "{anime}" not in text:
            await message.reply_text(
                "❌ Your template must include `{anime}`. Please send it again."
            )
            return

        settings["post_text"] = text
        save_settings(settings)
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ Post text updated.")
        return

    if action == "expiry":
        try:
            expiry = int(text)
            if expiry < 1:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ Send a valid positive number of minutes.")
            return

        settings = load_settings()
        settings["expiry_minutes"] = expiry
        save_settings(settings)
        context.user_data.pop("admin_action", None)
        await message.reply_text(f"✅ Expiry set to {expiry} minutes.")
        return


async def process_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not is_admin(user.id) or not message.photo:
        return

    action = context.user_data.get("admin_action")
    if action not in ("global_poster", "anime_poster_photo"):
        return

    settings = load_settings()
    file_id = message.photo[-1].file_id

    if action == "global_poster":
        settings["global_poster"] = file_id
        await message.reply_text("✅ Shared poster saved.")
    else:
        anime_title = context.user_data.get("poster_anime_name", "")
        settings.setdefault("posters", {})[normalize(anime_title)] = file_id
        await message.reply_text(f"✅ Poster saved for {anime_title}.")

    save_settings(settings)
    context.user_data.pop("admin_action", None)
    context.user_data.pop("poster_anime_name", None)


async def process_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or not is_admin(user.id):
        return

    if context.user_data.get("admin_action") != "import":
        return

    document = message.document
    if not document or not document.file_name.lower().endswith(".txt"):
        await message.reply_text("❌ Please upload a `.txt` file.")
        return

    tg_file = await context.bot.get_file(document.file_id)
    content = (await tg_file.download_as_bytearray()).decode(
        "utf-8", errors="replace"
    )

    anime_list = load_anime()
    added = 0
    skipped = 0

    for line in content.splitlines()[:MAX_BATCH_ENTRIES]:
        parts = [p.strip() for p in line.split(" - ", 2)]

        if len(parts) < 2:
            skipped += 1
            continue

        title, url = parts[0], parts[1]
        search_name = parts[2] if len(parts) > 2 else title

        if not title or not valid_url(url):
            skipped += 1
            continue

        exists = any(
            normalize(a["title"]) == normalize(title)
            or normalize(a["url"]) == normalize(url)
            for a in anime_list
        )

        if exists:
            skipped += 1
            continue

        anime_list.append({
            "title": title,
            "url": url,
            "search_name": search_name,
        })
        added += 1

    save_anime(anime_list)
    context.user_data.pop("admin_action", None)

    await message.reply_text(
        f"✅ Import completed!\nAdded: {added}\nSkipped: {skipped}"
    )


# ================= SEARCH HANDLER =================

async def search_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles normal text in private chats and groups.
    If no anime matches, ALWAYS sends the no-result message.
    """
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text:
        return

    query = message.text.strip()

    # Never treat commands as anime searches.
    if not query or query.startswith("/"):
        return

    # Admin input should be processed by the admin workflow.
    if user and is_admin(user.id) and context.user_data.get("admin_action"):
        await process_admin_text(update, context)
        return

    anime_list = load_anime()
    anime = find_anime(query, anime_list)

    if anime:
        settings = load_settings()
        await send_anime_result(message, anime, settings)
    else:
        await send_no_anime(message, query, anime_list)


# ================= ERROR HANDLER =================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "An exception occurred while handling an update:",
        exc_info=context.error,
    )


# ================= MAIN =================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_anime))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(admin_callback))

    # Admin photo and TXT uploads.
    app.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, process_admin_photo)
    )
    app.add_handler(
        MessageHandler(filters.Document.ALL & ~filters.COMMAND, process_admin_document)
    )

    # Admin workflow takes priority over normal search.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            process_admin_text,
        ),
        group=0,
    )

    # User search and the no-result response.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_anime,
        ),
        group=1,
    )

    app.add_error_handler(error_handler)

    logger.info("%s is starting...", BOT_TITLE)
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()