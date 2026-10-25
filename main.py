
import os
import json
import logging
import threading
import difflib
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================================================
# CONFIGURATION
# ==================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip().isdigit()
}

BOT_TITLE = "ANIFLIX ANIME INDEX BOT TAMIL"
MAX_BATCH_ENTRIES = 50

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "anime_db.txt"
SETTINGS_FILE = DATA_DIR / "anime_settings.json"

DEFAULT_POST_TEXT = (
    "🍥 {anime}\n\n"
    "🎧 Audio: Tamil Audio\n"
    "📺 Quality: 480p | 720p | 1080p\n\n"
    "👇 Click the below button to download!"
)

WELCOME_TEXT = (
    "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
    "Just type the anime you want to find! 🔎\n\n"
    "Use /list to get the available anime list.\n\n"
    "Powered By: @ANIFLIXANIMETAMIL"
)

NO_ANIME_TEXT = (
    "❌ No Anime Found!\n\n"
    "Please check the anime name and spelling. 🔎\n"
    "If the anime is missing, tell us in the support group.\n\n"
    "Use /list to get the available anime list."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==================================================
# FLASK SERVER FOR RENDER WEB SERVICE
# ==================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "ANIFLIX ANIME INDEX BOT TAMIL is running!", 200


@web_app.route("/health")
def health():
    return "OK", 200


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    web_app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
        threaded=True,
    )


# ==================================================
# DATABASE
# ==================================================

def valid_url(url):
    try:
        parsed = urlparse(url.strip())
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )
    except Exception:
        return False


def normalize(text):
    return " ".join(text.casefold().strip().split())


def load_anime():
    if not DB_FILE.exists():
        DB_FILE.write_text("", encoding="utf-8")

    anime_list = []

    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        parts = [part.strip() for part in line.split(" - ", 2)]

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
    lines = []

    for anime in anime_list:
        lines.append(
            f"{anime['title']} - {anime['url']} - {anime['search_name']}"
        )

    DB_FILE.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def load_settings():
    defaults = {
        "expiry_minutes": 10,
        "post_text": DEFAULT_POST_TEXT,
        "global_poster": None,
        "posters": {},
    }

    if not SETTINGS_FILE.exists():
        return defaults

    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as file:
            settings = json.load(file)

        for key, value in defaults.items():
            settings.setdefault(key, value)

        return settings

    except (json.JSONDecodeError, OSError):
        logger.exception("Could not load settings.")
        return defaults


def save_settings(settings):
    with SETTINGS_FILE.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


# ==================================================
# ANIME SEARCH
# ==================================================

def find_anime(search, anime_list):
    query = normalize(search)

    if not query:
        return None

    # Exact title or alternate-name match
    for anime in anime_list:
        if query in (
            normalize(anime["title"]),
            normalize(anime["search_name"]),
        ):
            return anime

    # Partial match
    for anime in anime_list:
        title = normalize(anime["title"])
        alt = normalize(anime["search_name"])

        if (
            query in title
            or title in query
            or query in alt
            or alt in query
        ):
            return anime

    # Fuzzy match for minor spelling mistakes
    choices = []

    for anime in anime_list:
        choices.append((normalize(anime["title"]), anime))
        choices.append((normalize(anime["search_name"]), anime))

    matched_names = difflib.get_close_matches(
        query,
        [name for name, _ in choices],
        n=1,
        cutoff=0.72,
    )

    if matched_names:
        for name, anime in choices:
            if name == matched_names[0]:
                return anime

    return None


async def send_no_anime(message, search, anime_list):
    text = NO_ANIME_TEXT

    if search.strip() and anime_list:
        suggestions = difflib.get_close_matches(
            search.strip(),
            [anime["title"] for anime in anime_list],
            n=3,
            cutoff=0.35,
        )

        if suggestions:
            text += "\n\n💡 Did you mean?\n"
            text += "\n".join(
                f"• {name}" for name in suggestions
            )

    await message.reply_text(text)


async def send_anime_result(message, anime, settings):
    template = settings.get("post_text", DEFAULT_POST_TEXT)

    try:
        post_text = template.format(
            anime=anime["title"],
            link=anime["url"],
            bot_title=BOT_TITLE,
            expiry=settings.get("expiry_minutes", 10),
        )
    except (KeyError, ValueError):
        post_text = DEFAULT_POST_TEXT.format(
            anime=anime["title"],
            link=anime["url"],
            bot_title=BOT_TITLE,
            expiry=settings.get("expiry_minutes", 10),
        )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🟢 DOWNLOAD NOW",
            url=anime["url"],
        )
    ]])

    poster = (
        settings.get("posters", {}).get(normalize(anime["title"]))
        or settings.get("global_poster")
    )

    if poster:
        try:
            await message.reply_photo(
                photo=poster,
                caption=post_text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Poster delivery failed; sending text instead.")

    await message.reply_text(
        post_text,
        reply_markup=keyboard,
    )


# ==================================================
# USER COMMANDS
# ==================================================

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

    text = "📚 ANIFLIX ANIME LIST\n\n"
    text += "\n".join(
        f"{index}. {anime['title']}"
        for index, anime in enumerate(anime_list, start=1)
    )

    # Split long lists into Telegram-sized messages.
    for index in range(0, len(text), 3500):
        await update.effective_message.reply_text(
            text[index:index + 3500]
        )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user or user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(
            "⛔ You are not authorized."
        )
        return

    context.user_data.pop("admin_action", None)

    await update.effective_message.reply_text(
        "⚙️ ANIFLIX ADMIN PANEL",
        reply_markup=admin_keyboard(),
    )


# ==================================================
# ADMIN PANEL
# ==================================================

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


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.message.reply_text("⛔ You are not authorized.")
        return

    action = query.data

    if action == "admin_cancel":
        context.user_data.pop("admin_action", None)
        await query.message.reply_text("✅ Cancelled.")
        return

    prompts = {
        "admin_add": (
            "add",
            "Send entries in this format:\n"
            "TITLE - URL - SEARCH NAME\n\n"
            "You can send multiple lines, up to 50 entries.",
        ),
        "admin_remove": (
            "remove",
            "Send the anime title or its URL to remove it.",
        ),
        "admin_import": (
            "import",
            "Upload a TXT file containing one entry per line:\n"
            "TITLE - URL - SEARCH NAME",
        ),
        "admin_poster": (
            "poster_choice",
            "Send `global` for one shared poster or `anime` for a separate poster.",
        ),
        "admin_post_text": (
            "post_text",
            "Send your new post template.\n\n"
            "Placeholders: {anime}, {link}, {bot_title}, {expiry}\n"
            "Send `reset default` to restore the default.",
        ),
        "admin_expiry": (
            "expiry",
            "Send the expiry time in minutes.",
        ),
        "admin_list": (
            None,
            "📚 Anime list:\n" + (
                "\n".join(
                    anime["title"] for anime in load_anime()
                ) or "No anime added."
            ),
        ),
    }

    if action not in prompts:
        return

    next_action, prompt = prompts[action]

    if next_action:
        context.user_data["admin_action"] = next_action

    await query.message.reply_text(prompt)


# ==================================================
# ADMIN TEXT INPUT
# ==================================================

async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or user.id not in ADMIN_IDS:
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    text = (message.text or "").strip()

    if action == "add":
        anime_list = load_anime()
        added = 0
        skipped = 0

        for line in text.splitlines()[:MAX_BATCH_ENTRIES]:
            parts = [p.strip() for p in line.split(" - ", 2)]

            if len(parts) < 2:
                skipped += 1
                continue

            title, url = parts[0], parts[1]
            search_name = parts[2] if len(parts) > 2 else title

            if not title or not valid_url(url):
                skipped += 1
                continue

            duplicate = any(
                normalize(a["title"]) == normalize(title)
                or normalize(a["url"]) == normalize(url)
                for a in anime_list
            )

            if duplicate:
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
            f"✅ Added: {added}\n⏭ Skipped: {skipped}"
        )
        return

    if action == "remove":
        anime_list = load_anime()
        before = len(anime_list)

        anime_list = [
            anime for anime in anime_list
            if normalize(anime["title"]) != normalize(text)
            and normalize(anime["url"]) != normalize(text)
            and normalize(anime["search_name"]) != normalize(text)
        ]

        save_anime(anime_list)
        context.user_data.pop("admin_action", None)

        if len(anime_list) < before:
            await message.reply_text("✅ Anime removed.")
        else:
            await message.reply_text("❌ Anime not found in database.")
        return

    if action == "poster_choice":
        if text.casefold() == "global":
            context.user_data["admin_action"] = "global_poster"
            await message.reply_text("Send the shared poster photo.")
        elif text.casefold() == "anime":
            context.user_data["admin_action"] = "anime_poster_name"
            await message.reply_text("Send the exact anime title.")
        else:
            await message.reply_text("Send `global` or `anime`.")
        return

    if action == "anime_poster_name":
        context.user_data["poster_anime_name"] = text
        context.user_data["admin_action"] = "anime_poster_photo"
        await message.reply_text("Now send that anime's poster photo.")
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
                "❌ Your template must include {anime}."
            )
            return

        settings["post_text"] = text
        save_settings(settings)
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ Post text updated.")
        return

    if action == "expiry":
        try:
            minutes = int(text)
            if minutes < 1:
                raise ValueError
        except ValueError:
            await message.reply_text("❌ Send a positive number of minutes.")
            return

        settings = load_settings()
        settings["expiry_minutes"] = minutes
        save_settings(settings)
        context.user_data.pop("admin_action", None)

        await message.reply_text(
            f"✅ Expiry set to {minutes} minutes."
        )


# ==================================================
# ADMIN PHOTO AND TXT UPLOADS
# ==================================================

async def process_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if (
        not message
        or not user
        or user.id not in ADMIN_IDS
        or not message.photo
    ):
        return

    action = context.user_data.get("admin_action")

    if action not in ("global_poster", "anime_poster_photo"):
        return

    settings = load_settings()
    file_id = message.photo[-1].file_id

    if action == "global_poster":
        settings["global_poster"] = file_id
        response = "✅ Shared poster saved."
    else:
        title = context.user_data.get("poster_anime_name", "")
        settings.setdefault("posters", {})[normalize(title)] = file_id
        response = f"✅ Poster saved for {title}."

    save_settings(settings)

    context.user_data.pop("admin_action", None)
    context.user_data.pop("poster_anime_name", None)

    await message.reply_text(response)


async def process_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not user or user.id not in ADMIN_IDS:
        return

    if context.user_data.get("admin_action") != "import":
        return

    document = message.document

    if not document or not document.file_name.lower().endswith(".txt"):
        await message.reply_text("❌ Please upload a TXT file.")
        return

    telegram_file = await context.bot.get_file(document.file_id)
    content = (
        await telegram_file.download_as_bytearray()
    ).decode("utf-8", errors="replace")

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

        duplicate = any(
            normalize(a["title"]) == normalize(title)
            or normalize(a["url"]) == normalize(url)
            for a in anime_list
        )

        if duplicate:
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
        f"✅ Import complete!\nAdded: {added}\nSkipped: {skipped}"
    )


# ==================================================
# MESSAGE ROUTING
# ==================================================

async def search_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text:
        return

    query = message.text.strip()

    if not query or query.startswith("/"):
        return

    # Admin inputs are handled by the admin workflow.
    if (
        user
        and user.id in ADMIN_IDS
        and context.user_data.get("admin_action")
    ):
        return

    anime_list = load_anime()
    anime = find_anime(query, anime_list)

    if anime:
        await send_anime_result(message, anime, load_settings())
    else:
        # This is the no-result response for private chats and groups.
        await send_no_anime(message, query, anime_list)


# ==================================================
# ERROR HANDLER
# ==================================================

async def error_handler(update, context):
    logger.error(
        "An error occurred while processing an update",
        exc_info=context.error,
    )


# ==================================================
# START BOT AND WEB SERVER
# ==================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing from Render environment variables."
        )

    # Start the HTTP server required by Render Web Services.
    threading.Thread(
        target=run_web_server,
        daemon=True,
        name="render-http-server",
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_anime))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(CallbackQueryHandler(admin_callback))

    # Admin uploads
    application.add_handler(
        MessageHandler(filters.PHOTO & ~filters.COMMAND, process_admin_photo)
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL & ~filters.COMMAND,
            process_admin_document,
        )
    )

    # Admin text is processed first.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            process_admin_text,
        ),
        group=0,
    )

    # Normal searches run separately, including the no-result reply.
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_anime,
        ),
        group=1,
    )

    application.add_error_handler(error_handler)

    logger.info("%s is starting...", BOT_TITLE)

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
