import os
import re
import json
import random
import logging
import threading
from copy import deepcopy
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
        "🎭 Genre: {genre}\n"
        "⭐ Rating: {rating}\n"
        "🎧 Audio: Tamil Audio\n\n"
        "📥 Click the button below to get your anime.\n\n"
        "Powered By: @ANIFLIXANIMETAMIL"
    ),
    "welcome_text": (
        "🍥 WELCOME TO ANIFLIX ANIME INDEX BOT TAMIL 🍥\n\n"
        "Just type the anime you want to search!\n\n"
        "Use /list to get the anime list.\n\n"
        "Powered By: @ANIFLIXANIMETAMIL"
    ),
    "no_anime_text": (
        "❌ No Anime Found!\n\n"
        "Check the spelling or use /list to see available anime."
    ),
    "global_poster": "",
    "poster_mode": "global",
    "random_posters": [],
    "posters": {},
    "search_counts": {},
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            settings_data = deepcopy(DEFAULT_SETTINGS)
            if isinstance(saved, dict):
                settings_data.update(saved)
            # Repair older or malformed settings without discarding saved values.
            # Upgrade the previous built-in default template to include genre/rating,
            # but preserve any custom template the admin already created.
            old_default_post_text = (
                "🍥 {anime}\n\n"
                "🎧 Audio: Tamil Audio\n"
                "📥 Click the button below to get your anime.\n\n"
                "Powered By: @ANIFLIXANIMETAMIL"
            )
            if settings_data.get("post_text") == old_default_post_text:
                settings_data["post_text"] = DEFAULT_SETTINGS["post_text"]
            if not isinstance(settings_data.get("posters"), dict):
                settings_data["posters"] = {}
            if not isinstance(settings_data.get("random_posters"), list):
                settings_data["random_posters"] = []
            if settings_data.get("poster_mode") not in {"global", "random"}:
                settings_data["poster_mode"] = "global"
            if not isinstance(settings_data.get("search_counts"), dict):
                settings_data["search_counts"] = {}
            return settings_data
        except (json.JSONDecodeError, OSError):
            logger.exception("Could not load settings")

    return deepcopy(DEFAULT_SETTINGS)


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

    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        # New format: BUTTON NAME - URL - GENRE - RATING - SEARCH NAME
        # Legacy 3-field lines are still accepted to protect existing data.
        parts = [part.strip() for part in line.split(" - ")]

        if len(parts) >= 5:
            button_name, url, genre, rating = parts[:4]
            search_name = " - ".join(parts[4:]).strip()
        elif len(parts) == 3:
            button_name, url, search_name = parts
            genre, rating = "Not specified", "N/A"
        else:
            continue

        if button_name and url.startswith(("https://", "http://")) and search_name:
            anime.append({
                "button_name": button_name,
                "url": url,
                "genre": genre or "Not specified",
                "rating": rating or "N/A",
                "search_name": search_name,
            })

    return anime


def save_anime(anime):
    lines = [
        f"{item['button_name']} - {item['url']} - {item.get('genre', 'Not specified')} - {item.get('rating', 'N/A')} - {item['search_name']}"
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

    if query in CHAT_PHRASES:
        return True

    words = query.split()

    if words[0] in {
        "hello", "hi", "hey", "thanks", "thank",
        "good", "bro", "guys", "dei", "machan",
    }:
        return True

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

    names = [normalize(item["search_name"]) for item in anime]
    close = get_close_matches(normalized_query, names, n=5, cutoff=0.65)

    suggestions = []
    for name in close:
        for item in anime:
            if normalize(item["search_name"]) == name:
                suggestions.append(item)
                break

    return [], suggestions


def random_anime_suggestions(context, exclude_names=None, limit=5):
    """Choose varied suggestions, avoiding the user's most recent suggestions."""
    anime = load_anime()
    if not anime:
        return []

    excluded = set(exclude_names or [])
    recent = context.user_data.get("recent_suggestions", [])
    candidates = [
        item for item in anime
        if normalize(item["search_name"]) not in excluded
        and normalize(item["search_name"]) not in recent
    ]

    # If the recent-history filter leaves too few choices, relax it but still
    # avoid duplicates in the current suggestion list.
    if len(candidates) < min(limit, len(anime)):
        candidates = [
            item for item in anime
            if normalize(item["search_name"]) not in excluded
        ]

    chosen = random.sample(candidates, k=min(limit, len(candidates)))
    context.user_data["recent_suggestions"] = [
        normalize(item["search_name"]) for item in chosen
    ]
    return chosen


def add_search_count(item):
    counts = settings.setdefault("search_counts", {})
    key = normalize(item["search_name"])
    try:
        counts[key] = int(counts.get(key, 0)) + 1
    except (TypeError, ValueError):
        counts[key] = 1
    save_settings()


def render_template(template, **values):
    """Replace supported placeholders without interpreting arbitrary braces."""
    result = str(template)
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


# =========================
# TELEGRAM HELPERS
# =========================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def mark_as_admin_input(context, message):
    # Prevent a completed admin command from also being treated as an anime search.
    context.user_data["skip_search_message_id"] = message.message_id


def welcome_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ONGOING ANIME", url=ONGOING_URL)],
        [InlineKeyboardButton("SUPPORT GROUP", url=SUPPORT_URL)],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = settings.get("welcome_text", DEFAULT_SETTINGS["welcome_text"])
    if update.message:
        await update.message.reply_text(text, reply_markup=welcome_keyboard())


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    anime = load_anime()

    if not anime:
        await update.effective_message.reply_text("The anime list is currently empty.")
        return

    lines = ["🍥 ANIFLIX ANIME INDEX TAMIL 🍥", ""]
    for index, item in enumerate(anime, start=1):
        lines.append(f"{index}. {item['button_name']}")

    message = ""
    for line in lines:
        if len(message) + len(line) + 1 > 3800:
            await update.effective_message.reply_text(message)
            message = ""
        message += line + "\n"

    if message:
        await update.effective_message.reply_text(message)


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counts = settings.get("search_counts", {})
    anime = load_anime()
    by_name = {normalize(item["search_name"]): item for item in anime}

    ranked = []
    for key, count in counts.items():
        try:
            number = int(count)
        except (TypeError, ValueError):
            continue
        item = by_name.get(normalize(key))
        if item and number > 0:
            ranked.append((number, item))

    ranked.sort(key=lambda pair: pair[0], reverse=True)

    if not ranked:
        await update.effective_message.reply_text(
            "📈 TRENDING ANIME SEARCHES\n\nNo searches have been recorded yet. Search for an anime to start the rankings!"
        )
        return

    lines = ["📈 TRENDING ANIME SEARCHES", "", "Most searched anime on ANIFLIX:", ""]
    for rank, (count, item) in enumerate(ranked[:10], start=1):
        lines.append(f"{rank}. {item['button_name']} — {count} searches")

    await update.effective_message.reply_text("\n".join(lines))


async def send_anime_result(
    update: Update,
    item,
    context: ContextTypes.DEFAULT_TYPE,
):
    # Count successful searches so /trending is based on real user searches.
    add_search_count(item)

    text = settings.get("post_text", DEFAULT_SETTINGS["post_text"])
    text = render_template(
        text,
        anime=item["button_name"],
        link=item["url"],
        bot_title="ANIFLIX ANIME INDEX BOT TAMIL",
        expiry=settings.get("expiry_minutes", 10),
        genre=item.get("genre", "Not specified"),
        rating=item.get("rating", "N/A"),
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📥 DOWNLOAD NOW", url=item["url"])
    ]])

    # Priority: title-specific poster, then random poster pool, then one global poster.
    poster = settings.get("posters", {}).get(
        normalize(item["search_name"]), ""
    )
    random_posters = settings.get("random_posters", [])
    if not poster and settings.get("poster_mode", "global") == "random" and random_posters:
        poster = random.choice(random_posters)
    if not poster:
        poster = settings.get("global_poster", "")

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


async def search_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text or not user:
        return

    if message.text.startswith("/"):
        return

    # The admin text handler marks a consumed message so this handler does not
    # accidentally process it as a public anime search.
    if context.user_data.get("skip_search_message_id") == message.message_id:
        context.user_data.pop("skip_search_message_id", None)
        return

    query = message.text.strip()

    if context.user_data.get("admin_action") and is_admin(user.id):
        return

    # Preserve the existing group behavior: only matching anime trigger a reply;
    # unrelated or unrecognized group conversation stays silent.
    if message.chat.type in ("group", "supergroup"):
        exact_or_partial, _suggestions = find_matches(query)
        if exact_or_partial:
            await send_anime_result(update, exact_or_partial[0], context)
        return

    exact_or_partial, _suggestions = find_matches(query)
    if exact_or_partial:
        await send_anime_result(update, exact_or_partial[0], context)
        return

    # Editable no-result text, followed by random inline buttons that open
    # each suggested anime's own download URL. Keep suggestions out of the text.
    no_result_text = settings.get(
        "no_anime_text", DEFAULT_SETTINGS["no_anime_text"]
    )
    no_result_text = render_template(no_result_text, query=query)

    suggestions = random_anime_suggestions(context, limit=5)
    suggestion_keyboard = None
    if suggestions:
        suggestion_rows = [
            [InlineKeyboardButton(
                f"🎬 {item['button_name']}"[:64],
                url=item["url"],
            )]
            for item in suggestions
        ]
        suggestion_keyboard = InlineKeyboardMarkup(suggestion_rows)

    await message.reply_text(
        no_result_text,
        reply_markup=suggestion_keyboard,
        disable_web_page_preview=True,
    )


# =========================
# ANIME ENTRY PARSER
# =========================

def parse_anime_entry(line):
    """Parse the current 5-field format, while accepting legacy 3-field lines."""
    parts = [part.strip() for part in line.split(" - ")]
    if len(parts) >= 5:
        button_name, url, genre, rating = parts[:4]
        search_name = " - ".join(parts[4:]).strip()
    elif len(parts) == 3:
        button_name, url, search_name = parts
        genre, rating = "Not specified", "N/A"
    else:
        return None

    if not button_name or not url.startswith(("https://", "http://")) or not search_name:
        return None
    return {
        "button_name": button_name,
        "url": url,
        "genre": genre or "Not specified",
        "rating": rating or "N/A",
        "search_name": search_name,
    }


# =========================
# ADMIN PANEL
# =========================


def main_admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Anime", callback_data="admin_add"),
            InlineKeyboardButton("➖ Remove Anime", callback_data="admin_remove"),
        ],
        [
            InlineKeyboardButton("📄 Import TXT", callback_data="admin_import"),
            InlineKeyboardButton("📚 View List", callback_data="admin_list"),
        ],
        [
            InlineKeyboardButton("🖼 Poster Settings", callback_data="admin_poster_settings"),
            InlineKeyboardButton("📝 Post Text", callback_data="admin_post_text"),
        ],
        [
            InlineKeyboardButton("👋 Welcome Message", callback_data="admin_welcome_text"),
            InlineKeyboardButton("❌ No Anime Message", callback_data="admin_no_anime_text"),
        ],
        [InlineKeyboardButton("⏱ Set Expiry", callback_data="admin_expiry")],
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    if not is_admin(user.id):
        await message.reply_text("You are not authorized to use this panel.")
        return

    context.user_data.pop("admin_action", None)
    await message.reply_text(
        "⚙️ ANIFLIX ADMIN PANEL\n\nChoose an action:",
        reply_markup=main_admin_keyboard(),
    )


async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if not is_admin(user.id):
        await query.message.reply_text("You are not authorized.")
        return

    action = query.data

    if action == "admin_back":
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(
            "⚙️ ANIFLIX ADMIN PANEL\n\nChoose an action:",
            reply_markup=main_admin_keyboard(),
        )
        return

    if action == "admin_poster_settings":
        context.user_data.pop("admin_action", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Global Poster", callback_data="admin_poster_global_menu")],
            [InlineKeyboardButton("🖼 Separate Poster", callback_data="admin_separate_poster")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await query.message.reply_text("🖼 POSTER SETTINGS\n\nChoose poster mode:", reply_markup=keyboard)
        return

    if action == "admin_poster_global_menu":
        context.user_data.pop("admin_action", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Global Poster", callback_data="admin_global_poster")],
            [InlineKeyboardButton("🎲 Random Poster", callback_data="admin_random_poster_menu")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_poster_settings")],
        ])
        await query.message.reply_text(
            "🌐 GLOBAL POSTER OPTIONS\n\n"
            "Global Poster: one image for every anime unless a separate poster is set.\n"
            "Random Poster: collect up to 20 images and randomly use one for each post.",
            reply_markup=keyboard,
        )
        return

    if action == "admin_random_poster_menu":
        context.user_data.pop("admin_action", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add More", callback_data="admin_random_add_more")],
            [InlineKeyboardButton("🗑️ Clear From First", callback_data="admin_random_clear_first")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_poster_global_menu")],
        ])
        current_count = len(settings.get("random_posters", []))
        await query.message.reply_text(
            f"🎲 RANDOM POSTER SETTINGS\n\nSaved posters: {current_count}/20\n\n"
            "Add More keeps your saved posters. Clear From First starts a fresh collection.",
            reply_markup=keyboard,
        )
        return

    if action == "admin_random_add_more":
        existing = list(settings.get("random_posters", []))[:20]
        if len(existing) >= 20:
            await query.message.reply_text("⚠️ You already have 20 posters. Clear the collection before adding more.")
            return
        context.user_data["random_poster_collection"] = existing
        context.user_data["random_poster_start_count"] = len(existing)
        context.user_data["admin_action"] = "admin_random_poster"
        await query.message.reply_text(
            f"➕ Send additional poster photos. You have {len(existing)}/20 saved; you can add {20-len(existing)} more.\n"
            "Tap Done when finished.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="random_poster_done")],
                [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel")],
            ]),
        )
        return

    if action == "admin_random_clear_first":
        context.user_data["admin_action"] = "admin_random_poster"
        context.user_data["random_poster_collection"] = []
        context.user_data["random_poster_start_count"] = 0
        await query.message.reply_text(
            "🗑️ Old random poster collection cleared from the new setup. Send up to 20 new poster photos, then tap Done.\n"
            "The previously saved collection will only be replaced when you tap Done.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="random_poster_done")],
                [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel")],
            ]),
        )
        return

    if action == "random_poster_done":
        collected = context.user_data.get("random_poster_collection", [])
        if not collected:
            await query.message.reply_text("Please send at least one poster photo first.")
            return
        settings["random_posters"] = collected[:20]
        settings["poster_mode"] = "random"
        save_settings()
        context.user_data.pop("random_poster_collection", None)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(
            f"✅ Random poster collection saved: {len(settings['random_posters'])} poster(s)."
        )
        return

    if action == "random_poster_cancel":
        context.user_data.pop("random_poster_collection", None)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text("❎ Random poster setup cancelled. Your saved poster collection was not changed.")
        return

    if action == "admin_list":
        anime = load_anime()
        if not anime:
            await query.message.reply_text("The anime list is empty.")
        else:
            lines = [
                f"{i}. {item['button_name']} | {item.get('genre', 'Not specified')} | {item.get('rating', 'N/A')} | {item['url']}"
                for i, item in enumerate(anime, start=1)
            ]
            for start_index in range(0, len(lines), 40):
                await query.message.reply_text("\n".join(lines[start_index:start_index + 40]))
        return

    instructions = {
        "admin_add": (
            "Send anime entries in this format:\n"
            "BUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n\n"
            "Example:\nNaruto - https://example.com/naruto - Action, Adventure - 8.4 - naruto\n\n"
            "You can send multiple lines."
        ),
        "admin_remove": "Send the anime title or URL you want to remove.",
        "admin_import": (
            "Upload a .txt file with one entry per line:\n"
            "BUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n"
            "Example:\nNaruto - https://example.com/naruto - Action, Adventure - 8.4 - naruto"
        ),
        "admin_global_poster": "Send one photo to use as the global poster.",
        "admin_random_poster": (
            "Send up to 20 poster photos, one photo per message.\n"
            "When finished, tap ✅ Done. The saved collection will be randomized across anime posts."
        ),
        "admin_post_text": (
            "Send your post template.\n"
            "Available placeholders: {anime}, {link}, {bot_title}, {expiry}, {genre}, {rating}"
        ),
        "admin_welcome_text": (
            "Send the new /start welcome message.\n"
            "The ONGOING ANIME and SUPPORT GROUP buttons will remain underneath it."
        ),
        "admin_no_anime_text": (
            "Send the new no-anime-found message.\n"
            "Optional placeholder: {query}\n"
            "Random anime suggestions will still appear underneath when anime are available."
        ),
        "admin_expiry": "Send the expiry value in minutes.",
        "admin_separate_poster": "Send the anime title first. Then send the photo for that anime.",
    }

    if action not in instructions:
        await query.message.reply_text("Unknown action.")
        return

    context.user_data["admin_action"] = action
    if action == "admin_random_poster":
        context.user_data["random_poster_collection"] = list(settings.get("random_posters", []))[:20]
    await query.message.reply_text(instructions[action])


async def process_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            entry = parse_anime_entry(line)
            if not entry:
                continue
            if any(normalize(item["search_name"]) == normalize(entry["search_name"]) for item in anime):
                continue
            anime.append(entry)
            added += 1

        save_anime(anime)
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text(f"✅ Added {added} anime entry/entries.")
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
        mark_as_admin_input(context, message)
        await message.reply_text(
            f"✅ Removed {removed} entry/entries." if removed
            else "❌ No matching anime was found."
        )
        return

    if action == "admin_post_text":
        settings["post_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text("✅ Post text updated.")
        return

    if action == "admin_welcome_text":
        if not text:
            await message.reply_text("Please send a welcome message that isn't empty.")
            return
        settings["welcome_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text("✅ Welcome message updated. It will be used by /start.")
        return

    if action == "admin_no_anime_text":
        if not text:
            await message.reply_text("Please send a message that isn't empty.")
            return
        settings["no_anime_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text("✅ No Anime Found message updated.")
        return

    if action == "admin_expiry":
        if not text.isdigit() or int(text) < 1:
            await message.reply_text("Please send a positive number of minutes.")
            return
        settings["expiry_minutes"] = int(text)
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text(f"✅ Expiry setting saved: {text} minutes.")
        return

    if action == "admin_separate_poster":
        target = normalize(text)
        matched = next((
            item for item in anime
            if target in {
                normalize(item["button_name"]),
                normalize(item["search_name"]),
            }
        ), None)
        if not matched:
            await message.reply_text("❌ Anime not found in your list. Send the exact anime title from /list.")
            return
        context.user_data["separate_poster_key"] = normalize(matched["search_name"])
        context.user_data["separate_poster_title"] = matched["button_name"]
        context.user_data["admin_action"] = "admin_separate_poster_photo"
        mark_as_admin_input(context, message)
        await message.reply_text(f"Now send the photo for {matched['button_name']}.")
        return


async def process_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.photo or not user or not is_admin(user.id):
        return

    action = context.user_data.get("admin_action")

    if action == "admin_global_poster":
        settings["global_poster"] = message.photo[-1].file_id
        settings["poster_mode"] = "global"
        save_settings()
        context.user_data.pop("admin_action", None)
        await message.reply_text("✅ Global poster updated.")
        return

    if action == "admin_separate_poster_photo":
        poster_key = context.user_data.get("separate_poster_key")
        title = context.user_data.get("separate_poster_title", "anime")
        if not poster_key:
            context.user_data.pop("admin_action", None)
            await message.reply_text("❌ Could not identify the anime. Please start again from Poster Settings.")
            return
        settings.setdefault("posters", {})[poster_key] = message.photo[-1].file_id
        save_settings()
        context.user_data.pop("admin_action", None)
        context.user_data.pop("separate_poster_key", None)
        context.user_data.pop("separate_poster_title", None)
        await message.reply_text(f"✅ Separate poster saved for {title}.")
        return

    if action == "admin_random_poster":
        collection = context.user_data.setdefault("random_poster_collection", [])
        if len(collection) >= 20:
            await message.reply_text(
                "⚠️ You already sent 20 posters. Tap ✅ Done to save this collection.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Done", callback_data="random_poster_done")],
                    [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel")],
                ]),
            )
            return

        collection.append(message.photo[-1].file_id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data="random_poster_done")],
            [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel")],
        ])
        await message.reply_text(
            f"✅ Poster received ({len(collection)}/20). Send another photo or tap ✅ Done.",
            reply_markup=keyboard,
        )


async def process_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        entry = parse_anime_entry(line)
        if not entry:
            continue
        if any(normalize(item["search_name"]) == normalize(entry["search_name"]) for item in anime):
            continue
        anime.append(entry)
        added += 1

    save_anime(anime)
    context.user_data.pop("admin_action", None)
    await message.reply_text(f"✅ Imported {added} anime entry/entries.")


# =========================
# START BOT
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Render Environment Variables.")

    threading.Thread(target=run_web_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", show_list))
    application.add_handler(CommandHandler("trending", trending))
    application.add_handler(CommandHandler("admin", admin_panel))

    application.add_handler(CallbackQueryHandler(admin_buttons))

    # Admin media handlers run before the general text-search handler.
    application.add_handler(MessageHandler(filters.PHOTO, process_admin_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, process_admin_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_text),
        group=0,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search_anime),
        group=1,
    )

    logger.info("Starting ANIFLIX ANIME INDEX BOT TAMIL")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
