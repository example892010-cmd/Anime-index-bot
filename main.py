
import os
import re
import json
import logging
import threading
from difflib import get_close_matches
from pathlib import Path
from flask import Flask, jsonify
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

# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}

ONGOING_URL = "https://t.me/+cVwTJSgsEvUzNWZl"
SUPPORT_URL = "https://t.me/+4YfcdPoSdK9jYmVl"

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "anime_db.txt"
SETTINGS_FILE = DATA_DIR / "anime_settings.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# =========================
# FLASK SERVER FOR RENDER
# =========================

web = Flask(__name__)


@web.route("/")
def home():
    return "ANIFLIX ANIME INDEX BOT TAMIL is running!"


@web.route("/health")
def health():
    return jsonify(status="OK"), 200


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    web.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
    )


# =========================
# LOCAL DATA
# =========================

DEFAULT_SETTINGS = {
    "expiry_minutes": 10,
    "post_text": (
        "🍥 {anime}\n\n"
        "🎧 Audio: Tamil Audio\n"
        "📥 Click the button below to get your anime.\n\n"
        "Powered By: @ANIFLIXANIMETAMIL"
    ),
    "global_poster": "",
    "posters": {},
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(
                SETTINGS_FILE.read_text(encoding="utf-8")
            )
            settings = DEFAULT_SETTINGS.copy()
            settings.update(saved)
            return settings
        except (json.JSONDecodeError, OSError):
            logger.exception("Could not load settings")

    return DEFAULT_SETTINGS.copy()


settings = load_settings()


def save_settings():
    SETTINGS_FILE.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_anime():
    anime = []

    if not DB_FILE.exists():
        DB_FILE.write_text("", encoding="utf-8")
        return anime

    for line in DB_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        parts = [part.strip() for part in line.split(" - ", 2)]

        if len(parts) != 3:
            continue

        button_name, url, search_name = parts

        if button_name and url.startswith(("https://", "http://")):
            anime.append({
                "button_name": button_name,
                "url": url,
                "search_name": search_name,
            })

    return anime


def save_anime(anime):
    lines = [
        f"{item['button_name']} - {item['url']} - {item['search_name']}"
        for item in anime
    ]
    DB_FILE.write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


# =========================
# SEARCH HELPERS
# =========================

def normalize(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


# Messages containing these phrases are normally conversations,
# not anime searches. Add more phrases here if needed.
CHAT_PHRASES = {
    "how are you",
    "how r u",
    "what are you doing",
    "what r u doing",
    "good morning",
    "good night",
    "good evening",
    "good afternoon",
    "thank you",
    "thanks",
    "welcome",
    "hello",
    "hi",
    "hey",
    "bro",
    "guys",
    "when will the update come",
    "when is the next update",
    "any updates",
    "what happened",
    "join now",
    "who are you",
    "are you there",
    "ok",
    "okay",
    "yes",
    "no",
    "super",
    "nice",
}


def looks_like_chat(message):
    query = normalize(message)

    if not query:
        return True

    # Very short conversational messages are ignored.
    if query in CHAT_PHRASES:
        return True

    words = query.split()

    # Greetings and common conversational openings.
    if words[0] in {
        "hello", "hi", "hey", "thanks", "thank",
        "good", "bro", "guys", "dei", "machan",
    }:
        return True

    # Ordinary questions are ignored unless the message
    # includes a known anime title.
    question_starters = {
        "how", "when", "where", "why", "who",
        "what", "is", "are", "can", "will",
        "did", "do", "does", "should", "any",
    }

    if words[0] in question_starters:
        return True

    return False


def find_matches(query):
    anime = load_anime()
    normalized_query = normalize(query)

    if not normalized_query:
        return [], []

    exact = []
    partial = []

    for item in anime:
        title = normalize(item["search_name"])
        button = normalize(item["button_name"])

        if normalized_query == title or normalized_query == button:
            exact.append(item)
        elif (
            normalized_query in title
            or normalized_query in button
            or title in normalized_query
        ):
            partial.append(item)

    if exact:
        return exact, []

    if partial:
        return partial[:10], []

    names = [
        normalize(item["search_name"])
        for item in anime
    ]

    close = get_close_matches(
        normalized_query,
        names,
        n=5,
        cutoff=0.65,
    )

    suggestions = []
    for name in close:
        for item in anime:
            if normalize(item["search_name"]) == name:
                suggestions.append(item)
                break

    return [], suggestions


# =========================
# TELEGRAM HELPERS
# =========================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def welcome_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "ONGOING ANIME",
                url=ONGOING_URL,
            ),
        ],
        [
            InlineKeyboardButton(
                "SUPPORT GROUP",
                url=SUPPORT_URL,
            ),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
        "Just type the anime you want to search!\n\n"
        "Use /list to get the anime list.\n\n"
        "Powered By: @ANIFLIXANIMETAMIL"
    )

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=welcome_keyboard(),
        )


async def show_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    anime = load_anime()

    if not anime:
        await update.effective_message.reply_text(
            "The anime list is currently empty."
        )
        return

    lines = ["🍥 ANIFLIX ANIME INDEX TAMIL 🍥", ""]

    for index, item in enumerate(anime, start=1):
        lines.append(f"{index}. {item['button_name']}")

    # Telegram messages have a length limit.
    message = ""
    for line in lines:
        if len(message) + len(line) + 1 > 3800:
            await update.effective_message.reply_text(message)
            message = ""
        message += line + "\n"

    if message:
        await update.effective_message.reply_text(message)


async def send_anime_result(
    update: Update,
    item,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = settings.get("post_text", DEFAULT_SETTINGS["post_text"])

    try:
        text = text.format(
            anime=item["button_name"],
            link=item["url"],
            bot_title="ANIFLIX ANIME INDEX BOT TAMIL",
            expiry=settings.get("expiry_minutes", 10),
        )
    except (KeyError, ValueError):
        text = f"🍥 {item['button_name']}\n\nClick the button below to get the anime."

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📥 DOWNLOAD NOW",
            url=item["url"],
        )
    ]])

    poster = settings.get("posters", {}).get(
        normalize(item["search_name"]), ""
    ) or settings.get("global_poster", "")

    if poster:
        try:
            await update.effective_message.reply_photo(
                photo=poster,
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Could not send poster; sending text instead")

    await update.effective_message.reply_text(
        text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def search_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text or not user:
        return

    # Ignore commands; command handlers process them separately.
    if message.text.startswith("/"):
        return

    query = message.text.strip()

    # Admins may be entering data for the admin panel.
    if context.user_data.get("admin_action") and is_admin(user.id):
        return

    # IMPORTANT: Do not respond to normal group conversation.
    if message.chat.type in ("group", "supergroup"):
        exact_or_partial, suggestions = find_matches(query)

        # Only reply if an actual anime was found.
        if exact_or_partial:
            # Long sentences are less likely to be anime titles.
            # A title found in the database is still allowed.
            await send_anime_result(
                update,
                exact_or_partial[0],
                context,
            )
            return

        # Unknown group messages are ignored silently.
        return

    # In private chat, unknown searches can show the no-result message.
    exact_or_partial, suggestions = find_matches(query)

    if exact_or_partial:
        await send_anime_result(
            update,
            exact_or_partial[0],
            context,
        )
        return

    text = "❌ No Anime Found!\n\n"

    if suggestions:
        text += "Did you mean:\n"
        for item in suggestions:
            text += f"• {item['button_name']}\n"
    else:
        text += "Check the spelling or use /list to see available anime."

    await message.reply_text(text)


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    if not is_admin(user.id):
        await message.reply_text("You are not authorized to use this panel.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Anime", callback_data="admin_add"),
            InlineKeyboardButton("➖ Remove Anime", callback_data="admin_remove"),
        ],
        [
            InlineKeyboardButton("📄 Import TXT", callback_data="admin_import"),
            InlineKeyboardButton("📚 View List", callback_data="admin_list"),
        ],
        [
            InlineKeyboardButton("🖼 Set Global Poster", callback_data="admin_global_poster"),
            InlineKeyboardButton("📝 Post Text", callback_data="admin_post_text"),
        ],
        [
            InlineKeyboardButton("⏱ Set Expiry", callback_data="admin_expiry"),
        ],
    ])

    await message.reply_text(
        "⚙️ ANIFLIX ADMIN PANEL\n\nChoose an action:",
        reply_markup=keyboard,
    )


async def admin_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    if not is_admin(user.id):
        await query.message.reply_text("You are not authorized.")
        return

    action = query.data

    instructions = {
        "admin_add": (
            "Send anime entries in this format:\n"
            "BUTTON NAME - URL - SEARCH NAME\n\n"
            "You can send multiple lines."
        ),
        "admin_remove": (
            "Send the anime title or URL you want to remove."
        ),
        "admin_import": (
            "Upload a .txt file with one entry per line:\n"
            "BUTTON NAME - URL - SEARCH NAME"
        ),
        "admin_global_poster": (
            "Send a photo to use as the global poster."
        ),
        "admin_post_text": (
            "Send your post template.\n"
            "Available placeholders: {anime}, {link}, {bot_title}, {expiry}"
        ),
        "admin_expiry": (
            "Send the expiry value in minutes."
        ),
    }

    if action == "admin_list":
        anime = load_anime()
        if not anime:
            await query.message.reply_text("The anime list is empty.")
        else:
            lines = [
                f"{i}. {item['button_name']} | {item['url']}"
                for i, item in enumerate(anime, start=1)
            ]
            for start_index in range(0, len(lines), 40):
                await query.message.reply_text(
                    "\n".join(lines[start_index:start_index + 40])
                )
        return

    context.user_data["admin_action"] = action
    await query.message.reply_text(instructions.get(action, "Unknown action."))


async def process_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text or not user:
        return

    action = context.user_data.get("admin_action")

    if not action or not is_admin(user.id):
        return

    text = message.text.strip()
    anime = load_anime()

    if action == "admin_add":
        added = 0
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(" - ", 2)]
            if len(parts) != 3:
                continue

            button_name, url, search_name = parts

            if not url.startswith(("https://", "http://")):
                continue

            if any(
                normalize(item["search_name"]) == normalize(search_name)
                for item in anime
            ):
                continue

            anime.append({
                "button_name": button_name,
                "url": url,
                "search_name": search_name,
            })
            added += 1

        save_anime(anime)
        context.user_data.pop("admin_action", None)

        await message.reply_text(
            f"✅ Added {added} anime entry/entries."
        )
        return

    if action == "admin_remove":
        updated = [
            item for item in anime
            if normalize(text) not in {
                normalize(item["button_name"]),
                normalize(item["search_name"]),
            }
            and text != item["url"]
        ]

        removed = len(anime) - len(updated)
        save_anime(updated)
        context.user_data.pop("admin_action", None)

        await message.reply_text(
            f"✅ Removed {removed} entry/entries."
            if removed
            else "❌ No matching anime was found."
        )
        return

    if action == "admin_post_text":
        settings["post_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ Post text updated.")
        return

    if action == "admin_expiry":
        if not text.isdigit() or int(text) < 1:
            await message.reply_text("Please send a positive number of minutes.")
            return

        settings["expiry_minutes"] = int(text)
        save_settings()
        context.user_data.pop("admin_action", None)
        await message.reply_text(
            f"✅ Expiry setting saved: {text} minutes."
        )
        return


async def process_admin_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.photo or not user:
        return

    action = context.user_data.get("admin_action")

    if not is_admin(user.id):
        return

    if action == "admin_global_poster":
        settings["global_poster"] = message.photo[-1].file_id
        save_settings()
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ Global poster updated.")


async def process_admin_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.document or not user:
        return

    if not is_admin(user.id):
        return

    if context.user_data.get("admin_action") != "admin_import":
        return

    document = message.document

    if not document.file_name or not document.file_name.lower().endswith(".txt"):
        await message.reply_text("Please upload a .txt file.")
        return

    file = await context.bot.get_file(document.file_id)
    content = await file.download_as_bytearray()

    imported_text = content.decode("utf-8", errors="replace")
    anime = load_anime()
    added = 0

    for line in imported_text.splitlines():
        parts = [p.strip() for p in line.split(" - ", 2)]
        if len(parts) != 3:
            continue

        button_name, url, search_name = parts

        if not url.startswith(("https://", "http://")):
            continue

        if any(
            normalize(item["search_name"]) == normalize(search_name)
            for item in anime
        ):
            continue

        anime.append({
            "button_name": button_name,
            "url": url,
            "search_name": search_name,
        })
        added += 1

    save_anime(anime)
    context.user_data.pop("admin_action", None)

    await message.reply_text(f"✅ Imported {added} anime entry/entries.")


# =========================
# START BOT
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add it in Render Environment Variables."
        )

    threading.Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", show_list))
    application.add_handler(CommandHandler("admin", admin_panel))

    application.add_handler(CallbackQueryHandler(admin_buttons))

    # Admin content handlers are checked before general searches.
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            process_admin_photo,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            process_admin_document,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            process_admin_text,
        ),
        group=0,
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_anime,
        ),
        group=1,
    )

    logger.info("Starting ANIFLIX ANIME INDEX BOT TAMIL")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
