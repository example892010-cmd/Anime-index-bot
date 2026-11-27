import asyncio
import html
import json
import logging
import os
import random
import re
import secrets
import shutil
import threading
import time
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
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
ACCESS_SECONDS = 6 * 60 * 60
LIST_DELETE_SECONDS = 10 * 60

# Use the configured persistent data directory when available. The original
# bot stored anime_db.txt / anime_settings.json in DATA_DIR (default: cwd),
# while the original Render blueprint exposed DB_PATH=/var/data/anime.db.
# The directory is now resolved safely without changing either file format.
if os.getenv("DATA_DIR"):
    DATA_DIR = Path(os.environ["DATA_DIR"]).expanduser()
elif os.getenv("DB_PATH"):
    DATA_DIR = Path(os.environ["DB_PATH"]).expanduser().parent
else:
    DATA_DIR = Path(".").expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "anime_db.txt"
SETTINGS_FILE = DATA_DIR / "anime_settings.json"


def migrate_legacy_data_files():
    """Preserve files from the old cwd location when moving to a mounted disk."""
    legacy_dir = Path(".").resolve()
    try:
        if DATA_DIR.resolve() == legacy_dir:
            return
    except OSError:
        return

    legacy_db = legacy_dir / "anime_db.txt"
    legacy_settings = legacy_dir / "anime_settings.json"

    if legacy_db.exists():
        try:
            if not DB_FILE.exists() or (DB_FILE.stat().st_size == 0 and legacy_db.stat().st_size > 0):
                shutil.copy2(legacy_db, DB_FILE)
            elif DB_FILE.exists() and legacy_db.stat().st_size > 0:
                # Keep every distinct anime row from both locations. Existing
                # persistent-disk rows remain first and are never overwritten.
                current_lines = DB_FILE.read_text(encoding="utf-8").splitlines()
                incoming_lines = legacy_db.read_text(encoding="utf-8").splitlines()

                def row_key(line):
                    parts = [part.strip() for part in line.split(" - ")]
                    if len(parts) >= 5:
                        raw_name = " - ".join(parts[4:])
                    elif len(parts) == 3:
                        raw_name = parts[2]
                    else:
                        raw_name = line
                    return re.sub(r"[^a-z0-9\s]", " ", raw_name.lower()).strip()

                seen = {row_key(line) for line in current_lines if row_key(line)}
                for line in incoming_lines:
                    key = row_key(line)
                    if key and key not in seen:
                        current_lines.append(line)
                        seen.add(key)
                DB_FILE.write_text("\n".join(current_lines) + ("\n" if current_lines else ""), encoding="utf-8")
        except OSError:
            logging.exception("Could not migrate legacy anime database; source was left untouched")

    if legacy_settings.exists():
        try:
            old_data = json.loads(legacy_settings.read_text(encoding="utf-8"))
            if not SETTINGS_FILE.exists() or SETTINGS_FILE.stat().st_size == 0:
                shutil.copy2(legacy_settings, SETTINGS_FILE)
            else:
                new_data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(old_data, dict) and isinstance(new_data, dict):
                    merged = dict(old_data)
                    merged.update(new_data)  # persistent-disk scalar values win
                    for key in ("posters", "search_counts", "anime_button_colors", "verification_links", "user_access", "request_dedupe"):
                        old_map = old_data.get(key, {}) if isinstance(old_data.get(key), dict) else {}
                        new_map = new_data.get(key, {}) if isinstance(new_data.get(key), dict) else {}
                        combined = dict(old_map)
                        for map_key, map_value in new_map.items():
                            if key in {"search_counts", "request_dedupe", "user_access"} and map_key in combined:
                                try:
                                    combined[map_key] = max(float(combined[map_key]), float(map_value))
                                except (TypeError, ValueError):
                                    combined[map_key] = map_value
                            else:
                                combined[map_key] = map_value
                        merged[key] = combined
                    merged["registered_users"] = list(dict.fromkeys(
                        list(old_data.get("registered_users", [])) + list(new_data.get("registered_users", []))
                    ))
                    for key in ("random_posters", "pending_requests", "pending_list_deletions", "movie_posts", "registered_groups", "poster_exclusions"):
                        values = []
                        seen_values = set()
                        for item in list(old_data.get(key, []) or []) + list(new_data.get(key, []) or []):
                            marker = (
                                item.get("id") if isinstance(item, dict) and item.get("id") is not None
                                else json.dumps(item, sort_keys=True, ensure_ascii=False)
                            )
                            if marker not in seen_values:
                                values.append(item)
                                seen_values.add(marker)
                            elif key == "pending_requests" and isinstance(item, dict):
                                for existing in values:
                                    if isinstance(existing, dict) and existing.get("id") == item.get("id"):
                                        existing["sent_to"] = sorted(set(existing.get("sent_to", [])) | set(item.get("sent_to", [])))
                                        break
                        merged[key] = values
                    temp = SETTINGS_FILE.with_suffix(SETTINGS_FILE.suffix + ".migrate.tmp")
                    temp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
                    temp.replace(SETTINGS_FILE)
        except (OSError, json.JSONDecodeError, TypeError):
            logging.exception("Could not merge legacy settings; source was left untouched")


migrate_legacy_data_files()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("aniflix_index_bot")

# =========================
# FLASK HEALTH SERVER
# =========================

web = Flask(__name__)
# The original Render blueprint used `main:app`; keep the alias for any older
# deployment configuration, although the supplied Render command uses python.
app = web


@web.route("/")
def home():
    return "ANIFLIX ANIME INDEX BOT TAMIL is running!"


@web.route("/health")
def health():
    return jsonify(status="OK"), 200


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    web.run(host="0.0.0.0", port=port, use_reloader=False)

# =========================
# DEFAULT SETTINGS + STORAGE
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
        "❌ NO ANIME FOUND\n\n"
        "The anime you searched for is not available.\n"
        "You can request it using the button below."
    ),
    "global_poster": "",
    "poster_mode": "global",
    "random_posters": [],
    "posters": {},
    "poster_exclusions": [],
    "search_counts": {},
    "default_button_color": "green",
    "anime_button_colors": {},
    "registered_users": [],
    "registered_groups": [],
    "movie_posts": [],
    "verification_enabled": False,
    "verification_links": {},
    "latest_verification_token": "",
    "custom_verification_url": "",
    "user_access": {},
    "verify_post_text": (
        "🍥 ANIFLIX ANIME INDEX BOT TAMIL\n\n"
        "Welcome! Tap VERIFY NOW to unlock private anime search for 6 hours."
    ),
    "verify_post_photo": "",
    "verify_button_text": "🟢 VERIFY NOW",
    "request_dedupe": {},
    "pending_requests": [],
    "pending_list_deletions": [],
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            data = deepcopy(DEFAULT_SETTINGS)
            if isinstance(saved, dict):
                data.update(saved)

            old_default_post_text = (
                "🍥 {anime}\n\n"
                "🎧 Audio: Tamil Audio\n"
                "📥 Click the button below to get your anime.\n\n"
                "Powered By: @ANIFLIXANIMETAMIL"
            )
            if data.get("post_text") == old_default_post_text:
                data["post_text"] = DEFAULT_SETTINGS["post_text"]

            expected_types = {
                "posters": dict,
                "poster_exclusions": list,
                "random_posters": list,
                "search_counts": dict,
                "anime_button_colors": dict,
                "registered_users": list,
                "registered_groups": list,
                "movie_posts": list,
                "verification_links": dict,
                "user_access": dict,
                "request_dedupe": dict,
                "pending_requests": list,
                "pending_list_deletions": list,
            }
            for key, expected in expected_types.items():
                if not isinstance(data.get(key), expected):
                    data[key] = deepcopy(DEFAULT_SETTINGS[key])

            if not isinstance(data.get("custom_verification_url"), str):
                data["custom_verification_url"] = ""

            if data.get("poster_mode") not in {"global", "random"}:
                data["poster_mode"] = "global"
            if data.get("default_button_color") not in {"red", "blue", "green"}:
                data["default_button_color"] = "green"
            if not isinstance(data.get("verification_enabled"), bool):
                data["verification_enabled"] = False
            return data
        except (json.JSONDecodeError, OSError):
            logger.exception("Could not load anime settings; using safe defaults")
    return deepcopy(DEFAULT_SETTINGS)


settings = load_settings()


def save_settings():
    """Atomically save settings so a partial write cannot corrupt the JSON file."""
    temp_file = SETTINGS_FILE.with_suffix(SETTINGS_FILE.suffix + ".tmp")
    temp_file.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp_file.replace(SETTINGS_FILE)


def normalize(value):
    value = str(value or "").lower().strip()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return " ".join(value.split())


def normalize_movie_title(value):
    """Normalize movie titles while retaining Tamil, Japanese, and other scripts."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    value = "".join(char if char.isalnum() else " " for char in value)
    return " ".join(value.split())


def aliases_for(search_name):
    """The Search Name field is authoritative; comma-separated aliases are allowed."""
    return [normalize(part) for part in str(search_name or "").split(",") if normalize(part)]


def parse_anime_entry(line):
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


def load_anime():
    if not DB_FILE.exists():
        DB_FILE.write_text("", encoding="utf-8")
        return []

    anime = []
    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        item = parse_anime_entry(line)
        if item:
            anime.append(item)
    return anime


def save_anime(anime):
    lines = [
        f"{item['button_name']} - {item['url']} - {item.get('genre', 'Not specified')} - "
        f"{item.get('rating', 'N/A')} - {item['search_name']}"
        for item in anime
    ]
    DB_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

# =========================
# TEXT / TELEGRAM HELPERS
# =========================


def bold_text(value):
    """Escape user-controlled text and display every bot message in bold HTML."""
    return "<b>" + html.escape(str(value), quote=False) + "</b>"


async def reply_bold(message, text, **kwargs):
    return await message.reply_text(bold_text(text), **kwargs)


async def send_bold(bot, chat_id, text, **kwargs):
    return await bot.send_message(chat_id=chat_id, text=bold_text(text), **kwargs)


def is_admin(user_id):
    return bool(user_id and user_id in ADMIN_IDS)


def mark_as_admin_input(context, message):
    context.user_data["skip_search_message_id"] = message.message_id


def welcome_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ONGOING ANIME", url=ONGOING_URL, style="primary")],
        [InlineKeyboardButton("SUPPORT GROUP", url=SUPPORT_URL, style="primary")],
    ])


def register_private_user(user):
    users = settings.setdefault("registered_users", [])
    uid = int(user.id)
    existing = {int(value) for value in users if str(value).isdigit()}
    if uid not in existing:
        users.append(uid)
        save_settings()


def register_group_chat(chat):
    """Remember groups/channels where Telegram delivers updates to this bot."""
    if not chat or chat.type not in {"group", "supergroup", "channel"}:
        return
    groups = settings.setdefault("registered_groups", [])
    group_id = int(chat.id)
    known = {int(value) for value in groups if str(value).lstrip("-").isdigit()}
    if group_id not in known:
        groups.append(group_id)
        save_settings()


def private_access_is_valid(user_id):
    if not settings.get("verification_enabled", False):
        return True
    raw_expiry = settings.get("user_access", {}).get(str(user_id))
    try:
        return float(raw_expiry) > time.time()
    except (TypeError, ValueError):
        return False


def format_ist(timestamp):
    value = float(timestamp)
    try:
        local_zone = ZoneInfo("Asia/Kolkata")
    except Exception:
        # India has no daylight-saving change; this fallback works on minimal
        # Python images where the operating-system timezone database is absent.
        local_zone = timezone(timedelta(hours=5, minutes=30))
    return datetime.fromtimestamp(value, local_zone).strftime("%d %b %Y, %I:%M %p IST")


async def deliver_pending_requests_to_admin(bot, admin_id):
    """Retry request notifications after an admin has opened the bot in PM."""
    pending = settings.setdefault("pending_requests", [])
    remaining = []
    changed = False
    for record in pending:
        sent_to = {int(value) for value in record.get("sent_to", []) if str(value).isdigit()}
        if int(admin_id) in sent_to:
            remaining.append(record)
            continue
        try:
            await send_bold(bot, int(admin_id), record.get("text", "📩 NEW ANIME REQUEST"))
            sent_to.add(int(admin_id))
            record["sent_to"] = sorted(sent_to)
            changed = True
        except Exception:
            remaining.append(record)
            continue
        if not set(ADMIN_IDS).issubset(sent_to):
            remaining.append(record)
    if changed or len(remaining) != len(pending):
        settings["pending_requests"] = remaining
        save_settings()


def verification_keyboard():
    token = settings.get("latest_verification_token", "")
    if not token or token not in settings.get("verification_links", {}):
        return None
    # The username is inserted later by send_verification_post.
    return token


async def send_verification_post(message, context, expired=False):
    text = settings.get("verify_post_text", DEFAULT_SETTINGS["verify_post_text"])
    if expired:
        text = "⏰ ACCESS EXPIRED\n\nYour 6-hour access to ANIFLIX Anime Index Bot has expired. Open the verification link below to activate another 6-hour session.\n\n" + text

    markup = None
    link = settings.get("custom_verification_url", "").strip()
    if not link:
        token = verification_keyboard()
        if token:
            me = await context.bot.get_me()
            link = f"https://t.me/{me.username}?start=verify_{token}"
    if link:
        label = settings.get("verify_button_text") or "🟢 VERIFY NOW"
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(label, url=link, style="success")
        ]])
    else:
        text += "\n\nVerification link is not available yet. Please contact the bot admin."

    caption = bold_text(text)
    photo = settings.get("verify_post_photo", "")
    if photo:
        try:
            await message.reply_photo(photo=photo, caption=caption, reply_markup=markup)
            return
        except Exception:
            logger.exception("Unable to send verification photo; falling back to text")
    await message.reply_text(caption, reply_markup=markup, disable_web_page_preview=True)


def ensure_private_access(update):
    """Return True for groups, disabled verification, or active private access."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type != "private":
        return True
    register_private_user(user)
    return private_access_is_valid(user.id)


def color_style(color):
    return {"red": "danger", "blue": "primary", "green": "success"}.get(color, "success")


def anime_button_color(item):
    key = normalize(item.get("search_name", ""))
    per_anime = settings.setdefault("anime_button_colors", {})
    color = per_anime.get(key, settings.get("default_button_color", "green"))
    return color if color in {"red", "blue", "green"} else "green"


def add_search_count(item):
    counts = settings.setdefault("search_counts", {})
    key = normalize(item["search_name"])
    try:
        counts[key] = int(counts.get(key, 0)) + 1
    except (TypeError, ValueError):
        counts[key] = 1
    save_settings()


def render_template(template, **values):
    result = str(template)
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result

# =========================
# SEARCH + SUGGESTIONS
# =========================

CHAT_PHRASES = {
    "how are you", "how r u", "what are you doing", "what r u doing",
    "good morning", "good night", "good evening", "good afternoon",
    "thank you", "thanks", "welcome", "hello", "hi", "hey", "bro",
    "guys", "when will the update come", "when is the next update",
    "any updates", "what happened", "join now", "who are you",
    "are you there", "ok", "okay", "yes", "no", "super", "nice",
}
QUESTION_STARTERS = {
    "how", "when", "where", "why", "who", "what", "is", "are",
    "can", "will", "did", "do", "does", "should", "any",
}


def looks_like_chat(message):
    query = normalize(message)
    if not query or query in CHAT_PHRASES:
        return True
    words = query.split()
    if words[0] in {"hello", "hi", "hey", "thanks", "thank", "good", "bro", "guys", "dei", "machan"}:
        return True
    if words[0] in QUESTION_STARTERS:
        return True
    # Ignore common sentence openings to avoid turning group conversation into
    # failed anime searches. Exact Search Name matches are checked before this.
    if words[0] in {"we", "you", "please", "my", "your", "our"}:
        return True
    if words[0] in {"the", "no"} and len(words) >= 3:
        return True
    if words[0] in {"i", "im", "ive"} and len(words) > 1 and words[1] in {"want", "need", "am", "was", "have", "think", "like", "love", "mean", "guess", "just", "will"}:
        return True
    if words[0] == "it" and len(words) > 1 and words[1] in {"is", "was", "will", "has", "does", "looks"}:
        return True
    return False


def find_exact_match(query):
    """Exact, case-insensitive match against Search Name only (or its aliases)."""
    normalized_query = normalize(query)
    if not normalized_query:
        return None
    for item in load_anime():
        if normalized_query in aliases_for(item["search_name"]):
            return item
    return None


def find_movie_match(query):
    """Exact title lookup for saved original movie / web-series posts."""
    key = normalize_movie_title(query)
    if not key:
        return None
    for item in settings.get("movie_posts", []):
        if not isinstance(item, dict):
            continue
        if item.get("normalized_title") == key or normalize_movie_title(item.get("title", "")) == key:
            return item
    return None


async def send_movie_result(update: Update, item, context: ContextTypes.DEFAULT_TYPE):
    """Copy the stored Telegram post so its media, caption entities and links stay intact."""
    message = update.effective_message
    if not message:
        return
    try:
        await context.bot.copy_message(
            chat_id=message.chat_id,
            from_chat_id=int(item["source_chat_id"]),
            message_id=int(item["source_message_id"]),
        )
    except Exception:
        logger.exception("Could not copy saved movie post %r", item.get("title"))
        await reply_bold(
            message,
            "❌ This saved movie post could not be delivered. Please contact the admin.",
        )


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def find_suggestions(query, limit=5):
    """Return likely saved title suggestions only; never send them as normal URLs."""
    q = normalize(query)
    if len(q) < 3:
        return []

    scored = []
    for item in load_anime():
        # Search Name is authoritative for both exact activation and suggestions.
        # The visible button title is deliberately not searched as an alias.
        candidates = aliases_for(item["search_name"])
        # Do not turn a longer message containing a complete saved alias into
        # an anime result/suggestion (for example, "naruto tamil").
        padded_query = f" {q} "
        if any(alias != q and f" {alias} " in padded_query for alias in candidates):
            continue
        best = 0.0
        for candidate in candidates:
            if len(candidate) < 3:
                continue
            score = similarity(q, candidate)
            if candidate.startswith(q) and len(q) >= 3:
                score = max(score, 0.74 + (len(q) / max(len(candidate), 1)) * 0.2)
            for token in candidate.split():
                if token.startswith(q) and len(q) >= 3:
                    score = max(score, 0.76 + (len(q) / max(len(token), 1)) * 0.18)
                if len(q) >= 4:
                    score = max(score, similarity(q, token))
            best = max(best, score)
        threshold = 0.68 if len(q) >= 4 else 0.78
        if best >= threshold:
            scored.append((best, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = []
    seen = set()
    for _, item in scored:
        key = normalize(item["search_name"])
        if key not in seen:
            results.append(item)
            seen.add(key)
        if len(results) >= limit:
            break
    return results


async def send_anime_result(update, item, context):
    add_search_count(item)
    template = settings.get("post_text", DEFAULT_SETTINGS["post_text"])
    text = render_template(
        template,
        anime=item["button_name"],
        link=item["url"],
        bot_title="ANIFLIX ANIME INDEX BOT TAMIL",
        expiry=settings.get("expiry_minutes", 10),
        genre=item.get("genre", "Not specified"),
        rating=item.get("rating", "N/A"),
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📥 DOWNLOAD NOW",
            url=item["url"],
            style=color_style(anime_button_color(item)),
        )
    ]])

    poster_key = normalize(item["search_name"])
    poster_excluded = poster_key in {
        normalize(value) for value in settings.get("poster_exclusions", [])
    }
    poster = "" if poster_excluded else settings.get("posters", {}).get(poster_key, "")
    random_posters = settings.get("random_posters", [])
    if not poster_excluded and not poster and settings.get("poster_mode", "global") == "random" and random_posters:
        poster = random.choice(random_posters)
    if not poster_excluded and not poster:
        poster = settings.get("global_poster", "")

    message = update.effective_message
    if poster:
        try:
            await message.reply_photo(photo=poster, caption=bold_text(text), reply_markup=keyboard)
            return
        except Exception:
            logger.exception("Could not send anime poster; falling back to text")
    await message.reply_text(
        bold_text(text),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def send_not_found(update, context, query_text):
    template = settings.get("no_anime_text", DEFAULT_SETTINGS["no_anime_text"])
    text = render_template(template, query=query_text)
    token = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:12]
    context.application.bot_data.setdefault("request_actions", {})[token] = {
        "title": query_text[:200],
        "requester_id": update.effective_user.id,
        "requester_name": update.effective_user.full_name or "Unknown",
        "username": update.effective_user.username or "",
        "chat_id": update.effective_chat.id,
        "chat_type": update.effective_chat.type,
        "chat_title": update.effective_chat.title or "" if update.effective_chat.type != "private" else "Private chat",
        "created_at": time.time(),
    }
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📩 REQUEST ANIME", callback_data=f"request:{token}", style="danger")
    ]])
    await update.effective_message.reply_text(bold_text(text), reply_markup=keyboard)


async def search_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not message.text or not user or not chat:
        return
    register_group_chat(chat)
    if message.text.startswith("/"):
        return
    if context.user_data.get("skip_search_message_id") == message.message_id:
        context.user_data.pop("skip_search_message_id", None)
        return
    if context.user_data.get("admin_action") and is_admin(user.id):
        return

    if chat.type == "private":
        register_private_user(user)
        if not private_access_is_valid(user.id):
            await send_verification_post(message, context)
            return

    query = message.text.strip()
    exact = find_exact_match(query)
    if exact:
        await send_anime_result(update, exact, context)
        return

    movie = find_movie_match(query)
    if movie:
        await send_movie_result(update, movie, context)
        return

    # Only short, title-like queries enter the request/suggestion path.
    if len(query) > 100 or len(query.split()) > 8:
        return

    suggestions = find_suggestions(query)
    if suggestions:
        action_store = context.application.bot_data.setdefault("suggestion_actions", {})
        buttons = []
        for item in suggestions:
            token = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:10]
            action_store[token] = normalize(item["search_name"])
            buttons.append(InlineKeyboardButton(
                item["button_name"], callback_data=f"suggest:{token}", style="primary"
            ))
        keyboard = InlineKeyboardMarkup([[button] for button in buttons])
        await message.reply_text(
            bold_text("🔍 NO EXACT MATCH\n\nDid you mean one of these saved anime?"),
            reply_markup=keyboard,
        )
        return

    if looks_like_chat(query):
        return

    await send_not_found(update, context, query)

# =========================
# USER ACCESS + REQUEST CALLBACKS
# =========================


def schedule_expiry_job(application, user_id, expiry):
    job_queue = application.job_queue
    if not job_queue:
        logger.error("JobQueue is unavailable; install python-telegram-bot[job-queue].")
        return
    job_name = f"verify-expiry-{user_id}"
    for job in job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    delay = max(0, float(expiry) - time.time())
    job_queue.run_once(
        verification_expired_job,
        when=delay,
        data={"user_id": int(user_id), "expected_expiry": float(expiry)},
        name=job_name,
    )


async def verification_expired_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if context.job else {}
    user_id = int(data.get("user_id", 0))
    expected = float(data.get("expected_expiry", 0))
    current = settings.get("user_access", {}).get(str(user_id))
    try:
        current = float(current)
    except (TypeError, ValueError):
        return
    if abs(current - expected) > 1 or current > time.time():
        return
    if not settings.get("verification_enabled", False):
        return
    try:
        await send_bold(
            context.bot,
            user_id,
            "⏰ ACCESS EXPIRED\n\nYour 6-hour access to ANIFLIX Anime Index Bot has expired. Open the permanent verification link to activate another 6-hour session.",
            disable_web_page_preview=True,
        )
        # Attach the current configured verification post/button when possible.
        url = settings.get("custom_verification_url", "").strip()
        if not url:
            token = verification_keyboard()
            if token:
                me = await context.bot.get_me()
                url = f"https://t.me/{me.username}?start=verify_{token}"
        if url:
            await send_bold(
                context.bot,
                user_id,
                "Tap below to verify again:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(settings.get("verify_button_text", "🟢 VERIFY NOW"), url=url, style="success")
                ]]),
            )
    except Exception:
        logger.info("Could not send expiry notification to user %s", user_id, exc_info=True)


async def activate_verification(update, context, token):
    message = update.effective_message
    user = update.effective_user
    if not user or not message:
        return
    register_private_user(user)
    links = settings.get("verification_links", {})
    if token not in links:
        await send_verification_post(message, context)
        return

    current_expiry = settings.get("user_access", {}).get(str(user.id))
    try:
        current_expiry = float(current_expiry)
    except (TypeError, ValueError):
        current_expiry = 0

    if current_expiry > time.time():
        await reply_bold(
            message,
            "✅ YOU ARE ALREADY VERIFIED!\n\nYour existing 6-hour access is still active.\nAccess expires: " + format_ist(current_expiry),
        )
        return

    expiry = time.time() + ACCESS_SECONDS
    settings.setdefault("user_access", {})[str(user.id)] = expiry
    save_settings()
    schedule_expiry_job(context.application, user.id, expiry)
    await reply_bold(
        message,
        "✅ YOU WERE VERIFIED!\n\nYou can use ANIFLIX Anime Index Bot for 6 hours.\nAccess expires: " + format_ist(expiry),
    )


async def generate_verification_link(bot):
    token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:18]
    while token in settings.setdefault("verification_links", {}):
        token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:18]
    settings["verification_links"][token] = {"created_at": time.time()}
    settings["latest_verification_token"] = token
    save_settings()
    me = await bot.get_me()
    return f"https://t.me/{me.username}?start=verify_{token}"


async def bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""

    if data.startswith("suggest:"):
        token = data.split(":", 1)[1]
        key = context.application.bot_data.get("suggestion_actions", {}).get(token)
        if not key:
            await query.message.reply_text(bold_text("This suggestion has expired. Please search again."))
            return
        if query.message.chat.type == "private" and not private_access_is_valid(query.from_user.id):
            await send_verification_post(query.message, context)
            return
        item = next((x for x in load_anime() if normalize(x["search_name"]) == key), None)
        if not item:
            await query.message.reply_text(bold_text("That anime is no longer in the saved list."))
            return
        await send_anime_result(update, item, context)
        return

    if data.startswith("request:"):
        token = data.split(":", 1)[1]
        action = context.application.bot_data.get("request_actions", {}).get(token)
        if not action:
            await query.message.reply_text(bold_text("This request button has expired. Please search again."))
            return
        if int(query.from_user.id) != int(action["requester_id"]):
            await query.answer("Only the person who searched can send this request.", show_alert=True)
            return

        dedupe_key = normalize(action["title"])
        # Limit repeated requests from one member for the same title to one per 10 minutes.
        key = f"{action['requester_id']}:{dedupe_key}"
        last = float(settings.setdefault("request_dedupe", {}).get(key, 0) or 0)
        if time.time() - last < 600:
            await query.message.reply_text(bold_text("✅ Your request was already sent recently. Please wait before requesting it again."))
            return
        settings["request_dedupe"][key] = time.time()
        save_settings()
        source = action.get("chat_title") or action.get("chat_type", "unknown")
        admin_text = (
            "📩 NEW ANIME REQUEST\n\n"
            f"📌 Requested Anime: {action['title']}\n"
            f"👤 Member: {action['requester_name']}\n"
            f"🆔 User ID: {action['requester_id']}\n"
        )
        if action.get("username"):
            admin_text += f"🔗 Username: @{action['username']}\n"
        admin_text += f"📍 Source: {source}\n⏰ Requested At: {format_ist(time.time())}"
        pending_record = {
            "id": token,
            "text": admin_text,
            "created_at": time.time(),
            "sent_to": [],
        }
        settings.setdefault("pending_requests", []).append(pending_record)
        save_settings()
        delivered = 0
        for admin_id in ADMIN_IDS:
            before_sent = set(pending_record.get("sent_to", []))
            await deliver_pending_requests_to_admin(context.bot, admin_id)
            if int(admin_id) in set(pending_record.get("sent_to", [])) or int(admin_id) in before_sent:
                delivered += 1
        await query.message.reply_text(
            bold_text("✅ Your anime request has been sent to the admin. Thank you!" if delivered else "✅ Your request has been saved. The admin will receive it when they open the bot in private chat."),
        )

# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    register_group_chat(chat)
    if chat.type == "private":
        register_private_user(user)
        if is_admin(user.id):
            await deliver_pending_requests_to_admin(context.bot, user.id)

    if chat.type == "private" and context.args:
        payload = context.args[0]
        if payload.startswith("verify_"):
            await activate_verification(update, context, payload[len("verify_"):])
            return

    if chat.type == "private" and settings.get("verification_enabled", False) and not private_access_is_valid(user.id):
        await send_verification_post(message, context)
        return

    await message.reply_text(
        bold_text(settings.get("welcome_text", DEFAULT_SETTINGS["welcome_text"])),
        reply_markup=welcome_keyboard(),
    )


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    if message.chat.type == "private":
        register_private_user(update.effective_user)
        if not private_access_is_valid(update.effective_user.id):
            await send_verification_post(message, context)
            return

    anime = load_anime()
    if not anime:
        await reply_bold(message, "The anime list is currently empty.")
        return

    lines = ["🍥 ANIFLIX ANIME INDEX TAMIL 🍥", ""]
    lines.extend(f"{index}. {item['button_name']}" for index, item in enumerate(anime, start=1))
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())

    sent_ids = []
    for chunk in chunks:
        sent = await message.reply_text(
            bold_text(chunk + "\n\n⏳ This list will be deleted automatically in 10 minutes.")
        )
        sent_ids.append(sent.message_id)
    record = {
        "id": secrets.token_hex(8),
        "chat_id": int(message.chat_id),
        "message_ids": sent_ids,
        "created_at": time.time(),
    }
    settings.setdefault("pending_list_deletions", []).append(record)
    save_settings()
    schedule_list_deletion(context.application, record)


def schedule_list_deletion(application, record):
    if not application.job_queue:
        logger.error("JobQueue unavailable; /list auto-delete cannot be scheduled.")
        return
    try:
        created = float(record.get("created_at", time.time()))
    except (TypeError, ValueError):
        created = time.time()
    delay = max(0, created + LIST_DELETE_SECONDS - time.time())
    application.job_queue.run_once(
        delete_list_job,
        when=delay,
        data=dict(record),
        name=f"list-delete-{record.get('id', record.get('chat_id'))}",
    )


async def delete_list_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if context.job else {}
    chat_id = data.get("chat_id")
    message_ids = data.get("message_ids", [])
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            logger.info("Could not delete /list message %s in chat %s", message_id, chat_id)

    record_id = data.get("id")
    pending = settings.setdefault("pending_list_deletions", [])
    settings["pending_list_deletions"] = [record for record in pending if record.get("id") != record_id]
    save_settings()
    try:
        await send_bold(
            context.bot,
            chat_id,
            "/list has been deleted. Send /list to view the available anime.",
        )
    except Exception:
        logger.info("Could not send /list expiry notice to chat %s", chat_id, exc_info=True)


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message.chat.type == "private":
        register_private_user(update.effective_user)
        if not private_access_is_valid(update.effective_user.id):
            await send_verification_post(message, context)
            return
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
        await reply_bold(message, "📈 TRENDING ANIME SEARCHES\n\nNo searches have been recorded yet.")
        return
    lines = ["📈 TRENDING ANIME SEARCHES", "", "Most searched anime on ANIFLIX:", ""]
    lines.extend(f"{rank}. {item['button_name']} — {count} searches" for rank, (count, item) in enumerate(ranked[:10], start=1))
    await reply_bold(message, "\n".join(lines))


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to use this command.")
        return
    users = {int(uid) for uid in settings.get("registered_users", []) if str(uid).isdigit()}
    await reply_bold(message, f"👥 ANIFLIX BOT USERS\n\nTotal registered PM users: {len(users)}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to use this command.")
        return
    source = message.reply_to_message if message else None
    if not source:
        await reply_bold(message, "Reply to the text/photo/video/document/sticker you want to broadcast, then send /broadcast.")
        return

    targets = sorted({int(uid) for uid in settings.get("registered_users", []) if str(uid).isdigit()})
    delivered = failed = 0
    for uid in targets:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=source.chat_id,
                message_id=source.message_id,
            )
            delivered += 1
        except Exception:
            failed += 1
            logger.info("Broadcast delivery failed for user %s", uid, exc_info=True)
        await asyncio.sleep(0.04)

    await reply_bold(
        message,
        f"📢 BROADCAST COMPLETED\n\nTotal users: {len(targets)}\n✅ Delivered: {delivered}\n❌ Failed: {failed}",
    )


async def verifylinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to generate verification links.")
        return
    link = await generate_verification_link(context.bot)
    try:
        await send_bold(
            context.bot,
            user.id,
            "🔗 PERMANENT VERIFICATION LINK GENERATED\n\nThis link itself does not expire. Each person who opens it receives their own 6-hour access period:\n\n" + link,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("OPEN VERIFICATION LINK", url=link, style="success")
            ]]),
            disable_web_page_preview=True,
        )
        await reply_bold(message, "✅ Your new permanent verification link was sent to your private chat.")
    except Exception:
        logger.exception("Could not send verification link to admin PM")
        await reply_bold(message, "❌ I could not PM you. Open the bot privately with /start, then run /verifylinks again.")


async def addposters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await reply_bold(update.effective_message, "You are not authorized to add posters.")
        return
    context.user_data.pop("admin_action", None)
    context.user_data["poster_caption_mode"] = True
    await reply_bold(
        update.effective_message,
        "🖼 POSTER UPLOAD MODE ENABLED\n\nSend each poster photo separately with its exact saved anime title in the photo caption.\nExample caption: Naruto\n\nThe poster is matched to the existing anime entry. Existing posters are not overwritten. Use /cancelposters when finished.",
    )


async def removeposters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to remove posters.")
        return
    context.user_data.pop("poster_caption_mode", None)
    context.user_data["admin_action"] = "admin_remove_poster"
    await reply_bold(
        message,
        "🗑 REMOVE ONE ANIME POSTER\n\nSend the exact saved anime title or Search Name. Only that anime's separate poster will be removed. Its title, download link, and anime entry will remain saved.",
    )


async def removeallposters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to remove posters.")
        return
    # Poster-only operation: never call save_anime(), edit DB_FILE, or alter
    # anime titles, URLs, movie posts, or other catalogue records here.
    per_anime = len(settings.get("posters", {}))
    random_count = len(settings.get("random_posters", []))
    had_global = bool(settings.get("global_poster"))
    settings["posters"] = {}
    settings["random_posters"] = []
    settings["global_poster"] = ""
    settings["poster_mode"] = "global"
    # Keep poster_exclusions untouched; it is per-title display configuration,
    # not saved anime data and not a stored poster.
    save_settings()
    context.user_data.pop("poster_caption_mode", None)
    context.user_data.pop("admin_action", None)
    await reply_bold(
        message,
        "✅ ALL SAVED POSTERS REMOVED\n\n"
        f"Separate posters: {per_anime}\nRandom posters: {random_count}\nGlobal poster: {'1' if had_global else '0'}\n\n"
        "Anime titles, saved download links, and movie posts were not changed.",
    )


async def editlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not is_admin(user.id):
        await reply_bold(message, "You are not authorized to replace the anime list.")
        return
    context.user_data.pop("poster_caption_mode", None)
    context.user_data["admin_action"] = "admin_edit_list"
    await reply_bold(
        message,
        "📄 REPLACE THE ANIME LIST\n\n"
        "Send the complete new list as text, one anime entry per line, in this format:\n"
        "BUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n\n"
        "The current list will be replaced only after at least one valid entry is received. An empty or incorrectly formatted list will not change your saved data. You may also upload a .txt file in this format.",
    )


async def cancelposters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await reply_bold(update.effective_message, "You are not authorized.")
        return
    context.user_data.pop("poster_caption_mode", None)
    context.user_data.pop("admin_action", None)
    await reply_bold(update.effective_message, "✅ Poster upload mode cancelled.")

# =========================
# ADMIN PANEL KEYBOARDS
# =========================


def main_admin_keyboard():
    verification_label = "🔐 Verification: ON" if settings.get("verification_enabled") else "🔐 Verification: OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Anime", callback_data="admin_add", style="success"), InlineKeyboardButton("➖ Remove Anime", callback_data="admin_remove", style="danger")],
        [InlineKeyboardButton("🎬 Add Movies", callback_data="admin_add_movies", style="success")],
        [InlineKeyboardButton("📄 Import TXT", callback_data="admin_import"), InlineKeyboardButton("📚 View List", callback_data="admin_list")],
        [InlineKeyboardButton("🖼 Poster Settings", callback_data="admin_poster_settings"), InlineKeyboardButton("📝 Post Text", callback_data="admin_post_text")],
        [InlineKeyboardButton("👋 Welcome Message", callback_data="admin_welcome_text"), InlineKeyboardButton("❌ No Anime Message", callback_data="admin_no_anime_text")],
        [InlineKeyboardButton("🎨 Button Colour", callback_data="admin_button_colors")],
        [InlineKeyboardButton("🔗 Verify Link", callback_data="admin_verify_link"), InlineKeyboardButton("🔗 Set Verify Link", callback_data="admin_set_verify_link")],
        [InlineKeyboardButton("📝 Verify Post", callback_data="admin_verify_post")],
        [InlineKeyboardButton(verification_label, callback_data="admin_verify_toggle", style="success" if settings.get("verification_enabled") else "danger")],
        [InlineKeyboardButton("⏱ Set Expiry", callback_data="admin_expiry")],
    ])


def color_keyboard(prefix):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 RED", callback_data=f"{prefix}:red", style="danger"),
        InlineKeyboardButton("🔵 BLUE", callback_data=f"{prefix}:blue", style="primary"),
        InlineKeyboardButton("🟢 GREEN", callback_data=f"{prefix}:green", style="success"),
    ], [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]])


def verify_post_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Edit Message", callback_data="admin_verify_text")],
        [InlineKeyboardButton("🖼 Set Photo", callback_data="admin_verify_photo"), InlineKeyboardButton("🗑 Remove Photo", callback_data="admin_verify_remove_photo")],
        [InlineKeyboardButton("🔘 Change Button Text", callback_data="admin_verify_button_text")],
        [InlineKeyboardButton("👁 Preview", callback_data="admin_verify_preview")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    if not is_admin(user.id):
        await reply_bold(message, "You are not authorized to use this panel.")
        return
    context.user_data.pop("admin_action", None)
    context.user_data.pop("poster_caption_mode", None)
    await message.reply_text(bold_text("⚙️ ANIFLIX ADMIN PANEL\n\nChoose an action:"), reply_markup=main_admin_keyboard())

# =========================
# ADMIN CALLBACKS
# =========================

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = query.from_user
    if not is_admin(user.id):
        await query.message.reply_text(bold_text("You are not authorized."))
        return
    action = query.data or ""

    if action in {"movie_post_save", "movie_post_edit", "movie_post_cancel"}:
        pending = context.user_data.get("pending_movie_post")
        if not pending:
            await query.message.reply_text(bold_text("This movie-post action has expired. Open Add Movies and send the post again."))
            return
        if action == "movie_post_cancel":
            context.user_data.pop("pending_movie_post", None)
            context.user_data.pop("admin_action", None)
            await query.message.reply_text(bold_text("❎ Movie post was not saved."))
            return
        if action == "movie_post_edit":
            context.user_data["admin_action"] = "admin_movie_title_edit"
            await query.message.reply_text(bold_text("Send the correct movie / web-series title. The post itself will remain unchanged."))
            return

        title = str(pending.get("title", "")).strip()
        title_key = normalize_movie_title(title)
        if not title_key:
            await query.message.reply_text(bold_text("❌ The title is empty. Tap Edit Title and send the correct title."))
            return
        duplicate = next((
            x for x in settings.get("movie_posts", [])
            if isinstance(x, dict) and normalize_movie_title(x.get("title", "")) == title_key
        ), None)
        if duplicate:
            await query.message.reply_text(bold_text(f"⚠️ A post for '{title}' is already saved. The existing post was not overwritten."))
            context.user_data.pop("pending_movie_post", None)
            context.user_data.pop("admin_action", None)
            return

        # Create a bot-owned copy in the admin's private chat. Later lookups copy
        # this saved message verbatim, preserving the original caption entities
        # and clickable link text rather than rebuilding the post from plain text.
        try:
            saved_message = await context.bot.copy_message(
                chat_id=int(user.id),
                from_chat_id=int(pending["source_chat_id"]),
                message_id=int(pending["source_message_id"]),
            )
        except Exception:
            logger.exception("Could not preserve the original movie post for %s", title)
            await query.message.reply_text(bold_text("❌ I could not save a copy of that post. Please send it to the bot again, making sure it is not protected from copying."))
            return

        record = {
            "title": title,
            "normalized_title": title_key,
            "source_chat_id": int(user.id),
            "source_message_id": int(saved_message.message_id),
            "created_at": time.time(),
        }
        settings.setdefault("movie_posts", []).append(record)
        save_settings()
        context.user_data.pop("pending_movie_post", None)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(bold_text(f"✅ Saved original post for: {title}\n\nUsers can now search this exact title."))
        await notify_new_content(context, [title], "movie / web-series")
        return

    # Avoid leaving caption-based poster mode active while editing unrelated
    # admin settings (for example, the verification photo).
    poster_actions = {
        "admin_poster_settings", "admin_addposters", "admin_poster_global_menu",
        "admin_global_poster", "admin_random_poster_menu", "admin_random_add_more",
        "admin_random_clear_first", "random_poster_done", "random_poster_cancel",
    }
    if action not in poster_actions and not action.startswith("admin_poster_"):
        context.user_data.pop("poster_caption_mode", None)

    if action == "admin_back":
        context.user_data.pop("admin_action", None)
        context.user_data.pop("pending_anime_color_key", None)
        context.user_data.pop("pending_anime_color_title", None)
        await query.message.reply_text(bold_text("⚙️ ANIFLIX ADMIN PANEL\n\nChoose an action:"), reply_markup=main_admin_keyboard())
        return

    if action == "admin_verify_toggle":
        settings["verification_enabled"] = not settings.get("verification_enabled", False)
        save_settings()
        if settings["verification_enabled"]:
            # Re-arm expiry notifications that may have elapsed while the
            # verification feature was switched OFF.
            for user_id, raw_expiry in settings.get("user_access", {}).items():
                try:
                    schedule_expiry_job(context.application, int(user_id), float(raw_expiry))
                except (TypeError, ValueError):
                    continue
        state = "ON" if settings["verification_enabled"] else "OFF"
        await query.message.reply_text(bold_text(f"✅ PM verification is now {state}. Group search remains free."), reply_markup=main_admin_keyboard())
        return

    if action == "admin_verify_link":
        await query.message.reply_text(
            bold_text("🔗 GENERATE A PERMANENT VERIFICATION LINK?\n\nThe link itself will not expire. Every user gets a separate 6-hour timer when they open it."),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ YES, GENERATE", callback_data="verify_link_yes", style="success"),
                InlineKeyboardButton("❌ NO", callback_data="verify_link_no", style="danger"),
            ]]),
        )
        return

    if action == "admin_set_verify_link":
        context.user_data["admin_action"] = "admin_set_verify_link"
        current = settings.get("custom_verification_url", "").strip()
        status = f"\n\nCurrent link: {current}" if current else "\n\nNo custom link is set. The built-in bot verification link is currently used."
        await query.message.reply_text(
            bold_text(
                "🔗 SET PM VERIFICATION LINK\n\n"
                "Send the HTTPS verification URL to use on the VERIFY NOW button. "
                "To clear the custom URL and return to the built-in bot verification link, send: clear"
                + status
                + "\n\nFor automatic 6-hour access activation, the URL must be a bot verification deep link generated by /verifylinks, or your external verification service must redirect users back to that link."
            )
        )
        return

    if action == "verify_link_no":
        await query.message.reply_text(bold_text("❎ Verification link generation cancelled."))
        return

    if action == "verify_link_yes":
        link = await generate_verification_link(context.bot)
        try:
            await send_bold(
                context.bot,
                user.id,
                "🔗 PERMANENT VERIFICATION LINK GENERATED\n\nThis link does not expire. Each user receives their own 6-hour access period:\n\n" + link,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("OPEN VERIFICATION LINK", url=link, style="success")
                ]]),
                disable_web_page_preview=True,
            )
            await query.message.reply_text(bold_text("✅ Your new permanent verification link was sent to your private chat."))
        except Exception:
            logger.exception("Could not PM generated verification link to admin")
            await query.message.reply_text(bold_text("❌ I could not PM you. Start the bot privately first, then generate the link again."))
        return

    if action == "admin_verify_post":
        await query.message.reply_text(
            bold_text("📝 VERIFY POST SETTINGS\n\nCustomize the message, optional photo, and green verification button."),
            reply_markup=verify_post_keyboard(),
        )
        return

    if action == "admin_verify_text":
        context.user_data["admin_action"] = "admin_verify_text"
        await query.message.reply_text(bold_text("Send the new verification message text."))
        return

    if action == "admin_verify_photo":
        context.user_data["admin_action"] = "admin_verify_photo"
        await query.message.reply_text(bold_text("Now send the photo to attach to the verification post."))
        return

    if action == "admin_verify_remove_photo":
        settings["verify_post_photo"] = ""
        save_settings()
        await query.message.reply_text(bold_text("✅ Verification photo removed."), reply_markup=verify_post_keyboard())
        return

    if action == "admin_verify_button_text":
        context.user_data["admin_action"] = "admin_verify_button_text"
        await query.message.reply_text(bold_text("Send the new Verify Now button label. The button will remain green."))
        return

    if action == "admin_verify_preview":
        text = settings.get("verify_post_text", DEFAULT_SETTINGS["verify_post_text"])
        markup = None
        link = settings.get("custom_verification_url", "").strip()
        if not link:
            token = verification_keyboard()
            if token:
                me = await context.bot.get_me()
                link = f"https://t.me/{me.username}?start=verify_{token}"
        if link:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(settings.get("verify_button_text", "🟢 VERIFY NOW"), url=link, style="success")
            ]])
        photo = settings.get("verify_post_photo", "")
        if photo:
            await query.message.reply_photo(photo=photo, caption=bold_text(text), reply_markup=markup)
        else:
            await query.message.reply_text(bold_text(text), reply_markup=markup)
        return

    if action == "admin_button_colors":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Set Default Colour", callback_data="admin_default_color")],
            [InlineKeyboardButton("🎯 Set Individual Anime Colour", callback_data="admin_individual_color")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await query.message.reply_text(bold_text(f"🎨 BUTTON COLOUR SETTINGS\n\nCurrent default: {settings.get('default_button_color', 'green').upper()}\nIndividual anime settings override the default."), reply_markup=keyboard)
        return

    if action == "admin_default_color":
        await query.message.reply_text(bold_text("Choose the default colour for anime download buttons:"), reply_markup=color_keyboard("default_color"))
        return

    if action.startswith("default_color:"):
        color = action.split(":", 1)[1]
        if color not in {"red", "blue", "green"}:
            return
        settings["default_button_color"] = color
        save_settings()
        await query.message.reply_text(bold_text(f"✅ Default anime button colour set to {color.upper()}. Individual overrides are unchanged."))
        return

    if action == "admin_individual_color":
        context.user_data["admin_action"] = "admin_individual_color"
        await query.message.reply_text(bold_text("Send the exact saved anime title or Search Name whose button colour you want to change."))
        return

    if action.startswith("anime_color:"):
        color = action.split(":", 1)[1]
        key = context.user_data.get("pending_anime_color_key")
        if color not in {"red", "blue", "green"} or not key:
            await query.message.reply_text(bold_text("Please select an anime first."))
            return
        settings.setdefault("anime_button_colors", {})[key] = color
        save_settings()
        title = context.user_data.pop("pending_anime_color_title", "Anime")
        context.user_data.pop("pending_anime_color_key", None)
        await query.message.reply_text(bold_text(f"✅ {title} button colour set to {color.upper()}."))
        return

    if action == "admin_poster_settings":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Global Poster", callback_data="admin_poster_global_menu")],
            [InlineKeyboardButton("🖼 Separate Poster", callback_data="admin_separate_poster")],
            [InlineKeyboardButton("🧾 Add Posters by Caption", callback_data="admin_addposters")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        ])
        await query.message.reply_text(bold_text("🖼 POSTER SETTINGS\n\nChoose poster mode:"), reply_markup=keyboard)
        return

    if action == "admin_add_movies":
        context.user_data.pop("pending_movie_post", None)
        context.user_data["admin_action"] = "admin_add_movie_post"
        await query.message.reply_text(
            bold_text(
                "🎬 ADD MOVIE / WEB SERIES POST\n\n"
                "Send the complete Telegram post (photo, video, document, or text) here. "
                "I will detect the title from the first meaningful title line and ask you to confirm it before saving.\n\n"
                "The saved post is copied as-is, so its caption formatting, embedded URLs, and clickable link text are preserved."
            )
        )
        return

    if action == "admin_addposters":
        context.user_data.pop("admin_action", None)
        context.user_data["poster_caption_mode"] = True
        await query.message.reply_text(bold_text("🖼 POSTER UPLOAD MODE ENABLED\n\nSend each photo with the exact saved anime title in its caption. Use /cancelposters when finished."))
        return

    if action == "admin_poster_global_menu":
        context.user_data.pop("admin_action", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Global Poster", callback_data="admin_global_poster")],
            [InlineKeyboardButton("🎲 Random Poster", callback_data="admin_random_poster_menu")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_poster_settings")],
        ])
        await query.message.reply_text(bold_text("🌐 GLOBAL POSTER OPTIONS\n\nGlobal Poster: one image for every anime unless a separate poster is set.\nRandom Poster: collect up to 20 images and randomly use one for each post."), reply_markup=keyboard)
        return

    if action == "admin_random_poster_menu":
        context.user_data.pop("admin_action", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add More", callback_data="admin_random_add_more")],
            [InlineKeyboardButton("🗑️ Clear From First", callback_data="admin_random_clear_first")],
            [InlineKeyboardButton("⬅️ Back", callback_data="admin_poster_global_menu")],
        ])
        current_count = len(settings.get("random_posters", []))
        await query.message.reply_text(bold_text(f"🎲 RANDOM POSTER SETTINGS\n\nSaved posters: {current_count}/20\n\nAdd More keeps your saved posters. Clear From First starts a fresh collection."), reply_markup=keyboard)
        return

    if action == "admin_random_add_more":
        existing = list(settings.get("random_posters", []))[:20]
        if len(existing) >= 20:
            await query.message.reply_text(bold_text("⚠️ You already have 20 posters. Clear the collection before adding more."))
            return
        context.user_data["random_poster_collection"] = existing
        context.user_data["admin_action"] = "admin_random_poster"
        await query.message.reply_text(
            bold_text(f"➕ Send additional poster photos. Saved: {len(existing)}/20. Tap Done when finished."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="random_poster_done", style="success")],
                [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel", style="danger")],
            ]),
        )
        return

    if action == "admin_random_clear_first":
        context.user_data["admin_action"] = "admin_random_poster"
        context.user_data["random_poster_collection"] = []
        await query.message.reply_text(
            bold_text("🗑️ Send up to 20 new random poster photos, then tap Done. The old collection is replaced only when you tap Done."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="random_poster_done", style="success")],
                [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel", style="danger")],
            ]),
        )
        return

    if action == "random_poster_done":
        collected = context.user_data.get("random_poster_collection", [])
        if not collected:
            await query.message.reply_text(bold_text("Please send at least one poster photo first."))
            return
        settings["random_posters"] = collected[:20]
        settings["poster_mode"] = "random"
        save_settings()
        context.user_data.pop("random_poster_collection", None)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(bold_text(f"✅ Random poster collection saved: {len(settings['random_posters'])} poster(s)."))
        return

    if action == "random_poster_cancel":
        context.user_data.pop("random_poster_collection", None)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(bold_text("❎ Random poster setup cancelled. Your saved poster collection was not changed."))
        return

    if action == "admin_list":
        anime = load_anime()
        if not anime:
            await query.message.reply_text(bold_text("The anime list is empty."))
        else:
            lines = [
                f"{i}. {item['button_name']} | {item.get('genre', 'Not specified')} | {item.get('rating', 'N/A')} | {item['url']}"
                for i, item in enumerate(anime, start=1)
            ]
            for start_index in range(0, len(lines), 35):
                await query.message.reply_text(bold_text("\n".join(lines[start_index:start_index + 35])))
        return

    instructions = {
        "admin_add": "Send anime entries in this format:\nBUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n\nExample:\nNaruto - https://example.com/naruto - Action, Adventure - 8.4 - naruto\n\nYou can send multiple lines.",
        "admin_remove": "Send the exact anime title, Search Name, or URL you want to remove.",
        "admin_import": "Upload a .txt file with one entry per line:\nBUTTON NAME - URL - GENRE - RATING - SEARCH NAME",
        "admin_global_poster": "Send one photo to use as the global poster.",
        "admin_random_poster": "Send up to 20 poster photos, one photo per message. Tap Done when finished.",
        "admin_post_text": "Send your anime result template. Placeholders: {anime}, {link}, {bot_title}, {expiry}, {genre}, {rating}",
        "admin_welcome_text": "Send the new /start welcome message.",
        "admin_no_anime_text": "Send the new no-anime-found message. Optional placeholder: {query}",
        "admin_expiry": "Send the expiry value in minutes.",
        "admin_separate_poster": "Send the exact anime title first. Then send the photo for that anime.",
    }
    if action not in instructions:
        await query.message.reply_text(bold_text("Unknown admin action."))
        return
    context.user_data["admin_action"] = action
    if action == "admin_random_poster":
        context.user_data["random_poster_collection"] = list(settings.get("random_posters", []))[:20]
    await query.message.reply_text(bold_text(instructions[action]))

# =========================
# ADMIN MESSAGE / MEDIA PROCESSORS
# =========================

async def notify_new_content(context, titles, content_type="anime"):
    if not titles:
        return
    users = sorted({int(uid) for uid in settings.get("registered_users", []) if str(uid).isdigit()})
    groups = sorted({int(gid) for gid in settings.get("registered_groups", []) if str(gid).lstrip("-").isdigit()})
    targets = [("private", uid) for uid in users] + [("group", gid) for gid in groups]
    if not targets:
        return
    normalized_kind = str(content_type or "anime").strip().lower()
    header = "🆕 NEW MOVIE / WEB SERIES ADDED 👇" if "movie" in normalized_kind else "🆕 NEW ANIME ADDED 👇"
    footer = "📋 Copy the title and paste it here to get the post." if "movie" in normalized_kind else "📋 Copy the anime title and paste it here to get the anime."
    # Keep each Telegram message below the 4096-character message limit.
    chunks = []
    current = header + "\n\n"
    for title in titles:
        line = f"• {title}\n"
        if len(current) + len(line) > 3300:
            chunks.append(current.rstrip())
            current = header + "\n\n"
        current += line
    current += f"\n{footer}\n\nPowered By: @ANIFLIXANIMETAMIL"
    chunks.append(current)
    for target_type, target_id in targets:
        for chunk in chunks:
            try:
                await send_bold(context.bot, target_id, chunk)
            except Exception:
                logger.info("Could not send new-%s notification to %s %s", normalized_kind, target_type, target_id, exc_info=True)
                break
        await asyncio.sleep(0.03)


async def notify_new_anime(context, titles):
    """Compatibility wrapper used by the existing anime add/import workflows."""
    await notify_new_content(context, titles, "anime")


def detect_movie_title_from_post(message):
    """Read a likely title from the post without changing its original content."""
    raw = message.text or message.caption or ""
    if not raw:
        return ""

    ignored_prefixes = (
        "genre", "genres", "audio", "quality", "download", "how to open",
        "how to download", "how to watch",
        "cast", "director", "rating", "imdb", "language", "runtime",
        "release date", "story", "description", "size", "uploaded",
        "season", "episode", "available in", "movie details",
    )
    for raw_line in raw.splitlines()[:10]:
        line = raw_line.strip()
        if not line:
            continue
        # If a pasted post contains Markdown link syntax, use only its visible
        # text for detection. The original Telegram message is never rewritten.
        line = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", line)
        line = re.sub(r"^(?:title|movie|movie name|web series|series name|anime name)\s*[:：\-]\s*", "", line, flags=re.IGNORECASE)
        line = line.strip()

        # Drop leading emoji / decorative punctuation, e.g. "⭐ Scene (2026)".
        while line and not line[0].isalnum():
            line = line[1:]
        line = line.strip("*_`~ ")
        # Drop trailing decorative symbols but preserve title punctuation such
        # as the closing bracket in "Scene (2026)".
        while line and unicodedata.category(line[-1]).startswith("S"):
            line = line[:-1].rstrip()
        line = re.sub(r"\s+", " ", line).strip(" \t|—–-:：")
        if not line:
            continue
        # Release years are metadata, not part of the saved searchable title.
        # Examples: "DC (2026)" -> "DC", "Scene [2026]" -> "Scene".
        line = re.sub(r"\s*[([{]\s*(?:19|20)\d{2}\s*[)\]}]\s*$", "", line).strip()
        key = normalize(line)
        if any(key.startswith(prefix) for prefix in ignored_prefixes):
            continue
        if len(line) > 120 or sum(1 for char in line if char.isalpha()) < 2:
            continue
        if re.fullmatch(r"https?://\S+", line, flags=re.IGNORECASE):
            continue
        return line
    return ""


async def process_admin_movie_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture one admin-sent post, detect its title, and ask for confirmation."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_admin(user.id):
        return
    if context.user_data.get("admin_action") != "admin_add_movie_post":
        return
    if message.chat.type != "private":
        await reply_bold(message, "Please send movie posts to the bot in its private chat so I can keep a saved original copy for future searches.")
        return
    if message.text and message.text.startswith("/"):
        return

    title = detect_movie_title_from_post(message)
    if not title:
        await reply_bold(
            message,
            "❌ I could not confidently detect the title. Please send the post again with the movie / series title on its own first meaningful line, or cancel from the admin panel.",
        )
        return

    context.user_data.pop("admin_action", None)
    context.user_data["pending_movie_post"] = {
        "title": title,
        "source_chat_id": int(message.chat_id),
        "source_message_id": int(message.message_id),
    }
    mark_as_admin_input(context, message)
    await message.reply_text(
        bold_text(
            f"🔎 DETECTED TITLE\n\n{title}\n\n"
            "Check the title carefully. Confirm to save the original post, or edit the title before saving."
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm & Save", callback_data="movie_post_save", style="success")],
            [InlineKeyboardButton("✏️ Edit Title", callback_data="movie_post_edit", style="primary")],
            [InlineKeyboardButton("❎ Cancel", callback_data="movie_post_cancel", style="danger")],
        ]),
    )


async def track_group_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remember any group where Telegram delivers an update to this bot."""
    register_group_chat(update.effective_chat)


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

    if action == "admin_remove_poster":
        target = normalize(text)
        matched = next(
            (item for item in anime if target == normalize(item["button_name"]) or target in aliases_for(item["search_name"])),
            None,
        )
        if not matched:
            await reply_bold(message, "❌ Anime not found. Send the exact saved title or Search Name from /list.")
            return
        key = normalize(matched["search_name"])
        poster = settings.setdefault("posters", {}).pop(key, None)
        exclusions = settings.setdefault("poster_exclusions", [])
        if key not in {normalize(value) for value in exclusions}:
            exclusions.append(key)
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        if poster:
            await reply_bold(message, f"✅ Separate poster removed for {matched['button_name']}. Its title and download link are unchanged.")
        else:
            await reply_bold(message, f"ℹ️ No separate poster was saved for {matched['button_name']}. Nothing else was changed.")
        return

    if action == "admin_set_verify_link":
        if text.lower() in {"clear", "none", "off", "remove"}:
            settings["custom_verification_url"] = ""
            save_settings()
            context.user_data.pop("admin_action", None)
            mark_as_admin_input(context, message)
            await reply_bold(message, "✅ Custom verification link cleared. The built-in bot verification link will be used again.")
            return
        parsed = urlparse(text)
        if parsed.scheme != "https" or not parsed.netloc:
            await reply_bold(message, "❌ Send a valid HTTPS URL, or send 'clear' to use the built-in verification link.")
            return
        settings["custom_verification_url"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(
            message,
            "✅ Custom PM verification link saved. It will appear on the VERIFY NOW button whenever PM verification is ON. For automatic 6-hour access, the link must be a /verifylinks deep link or return users to one after external verification.",
        )
        return

    if action == "admin_movie_title_edit":
        pending = context.user_data.get("pending_movie_post")
        title = re.sub(r"\s+", " ", text).strip()
        if not pending:
            context.user_data.pop("admin_action", None)
            await reply_bold(message, "❌ The pending post has expired. Open Add Movies and send it again.")
            return
        if not title or len(title) > 120 or not any(char.isalpha() for char in title):
            await reply_bold(message, "Send a valid movie / web-series title (1–120 characters).")
            return
        pending["title"] = title
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text(
            bold_text(f"🔎 TITLE TO SAVE\n\n{title}\n\nConfirm this title to save the original post."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm & Save", callback_data="movie_post_save", style="success")],
                [InlineKeyboardButton("✏️ Edit Title", callback_data="movie_post_edit", style="primary")],
                [InlineKeyboardButton("❎ Cancel", callback_data="movie_post_cancel", style="danger")],
            ]),
        )
        return

    if action == "admin_edit_list":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        replacement = []
        seen_names = set()
        invalid_lines = []
        for line_number, line in enumerate(lines, start=1):
            entry = parse_anime_entry(line)
            if not entry:
                invalid_lines.append(line_number)
                continue
            key = normalize(entry["search_name"])
            if key and key not in seen_names:
                replacement.append(entry)
                seen_names.add(key)
        if not replacement or invalid_lines:
            bad = ", ".join(str(number) for number in invalid_lines[:15])
            details = f"\nInvalid line(s): {bad}" if bad else ""
            await reply_bold(
                message,
                "❌ The new list was not saved. Every non-empty line must use this format:\n"
                "BUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n"
                f"Valid entries detected: {len(replacement)}.{details}\n\nYour existing list is unchanged.",
            )
            return
        old_names = {normalize(item["search_name"]) for item in anime}
        save_anime(replacement)
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        newly_added = [item["button_name"] for item in replacement if normalize(item["search_name"]) not in old_names]
        await reply_bold(message, f"✅ Anime list replaced successfully.\nSaved entries: {len(replacement)}\nNew titles: {len(newly_added)}")
        if newly_added:
            await notify_new_anime(context, newly_added)
        return

    if action == "admin_add":
        added_titles = []
        for line in text.splitlines():
            entry = parse_anime_entry(line)
            if not entry:
                continue
            existing_aliases = {alias for item in anime for alias in aliases_for(item["search_name"])}
            if any(alias in existing_aliases for alias in aliases_for(entry["search_name"])):
                continue
            anime.append(entry)
            added_titles.append(entry["button_name"])
        save_anime(anime)
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, f"✅ Added {len(added_titles)} new anime entr(y/ies).")
        if added_titles:
            await notify_new_anime(context, added_titles)
        return

    if action == "admin_remove":
        updated = [item for item in anime if normalize(text) not in {normalize(item["button_name"]), *aliases_for(item["search_name"])} and text != item["url"]]
        removed = len(anime) - len(updated)
        save_anime(updated)
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, f"✅ Removed {removed} entry/entries." if removed else "❌ No matching anime was found.")
        return

    if action == "admin_individual_color":
        target = normalize(text)
        matched = next((item for item in anime if target == normalize(item["button_name"]) or target in aliases_for(item["search_name"])), None)
        if not matched:
            await reply_bold(message, "❌ Anime not found. Send the exact saved title or Search Name.")
            return
        context.user_data["pending_anime_color_key"] = normalize(matched["search_name"])
        context.user_data["pending_anime_color_title"] = matched["button_name"]
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await message.reply_text(bold_text(f"Choose the button colour for {matched['button_name']}:") , reply_markup=color_keyboard("anime_color"))
        return

    if action == "admin_post_text":
        settings["post_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, "✅ Anime post text updated.")
        return

    if action == "admin_welcome_text":
        if not text:
            await reply_bold(message, "Please send a welcome message that isn't empty.")
            return
        settings["welcome_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, "✅ Welcome message updated.")
        return

    if action == "admin_no_anime_text":
        if not text:
            await reply_bold(message, "Please send a message that isn't empty.")
            return
        settings["no_anime_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, "✅ No Anime Found message updated.")
        return

    if action == "admin_expiry":
        if not text.isdigit() or int(text) < 1 or int(text) > 1440:
            await reply_bold(message, "Send a positive number of minutes, up to 1440.")
            return
        settings["expiry_minutes"] = int(text)
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, f"✅ Expiry setting saved: {text} minutes.")
        return

    if action == "admin_separate_poster":
        target = normalize(text)
        matched = next((item for item in anime if target == normalize(item["button_name"]) or target in aliases_for(item["search_name"])), None)
        if not matched:
            await reply_bold(message, "❌ Anime not found in your list. Send the exact anime title from /list.")
            return
        context.user_data["separate_poster_key"] = normalize(matched["search_name"])
        context.user_data["separate_poster_title"] = matched["button_name"]
        context.user_data["admin_action"] = "admin_separate_poster_photo"
        mark_as_admin_input(context, message)
        await reply_bold(message, f"Now send the photo for {matched['button_name']}.")
        return

    if action == "admin_verify_text":
        if not text:
            await reply_bold(message, "Verification message cannot be empty.")
            return
        settings["verify_post_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, "✅ Verification post message updated.")
        return

    if action == "admin_verify_button_text":
        if not text or len(text) > 64:
            await reply_bold(message, "Button text must contain 1 to 64 characters.")
            return
        settings["verify_button_text"] = text
        save_settings()
        context.user_data.pop("admin_action", None)
        mark_as_admin_input(context, message)
        await reply_bold(message, "✅ Verification button text updated. It remains green.")
        return


async def process_admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not message.photo or not user or not is_admin(user.id):
        return

    # Caption-based workflow: each image must carry its own exact anime title.
    if context.user_data.get("poster_caption_mode"):
        caption = (message.caption or "").strip()
        if not caption:
            await reply_bold(message, "❌ Add the exact saved anime title in this photo's caption.")
            return
        target = normalize(caption)
        matched = next((item for item in load_anime() if target == normalize(item["button_name"]) or target in aliases_for(item["search_name"])), None)
        if not matched:
            await reply_bold(message, "❌ No exact saved anime matched this caption. Poster was not saved.")
            return
        key = normalize(matched["search_name"])
        posters = settings.setdefault("posters", {})
        if posters.get(key):
            await reply_bold(message, f"⚠️ A poster is already saved for {matched['button_name']}. It was not overwritten.")
            return
        posters[key] = message.photo[-1].file_id
        settings["poster_exclusions"] = [value for value in settings.get("poster_exclusions", []) if normalize(value) != key]
        save_settings()
        await reply_bold(message, f"✅ Poster saved for {matched['button_name']}.")
        return

    action = context.user_data.get("admin_action")
    if action == "admin_global_poster":
        settings["global_poster"] = message.photo[-1].file_id
        settings["poster_mode"] = "global"
        save_settings()
        context.user_data.pop("admin_action", None)
        await reply_bold(message, "✅ Global poster updated.")
        return

    if action == "admin_separate_poster_photo":
        poster_key = context.user_data.get("separate_poster_key")
        title = context.user_data.get("separate_poster_title", "anime")
        if not poster_key:
            context.user_data.pop("admin_action", None)
            await reply_bold(message, "❌ Could not identify the anime. Please start again from Poster Settings.")
            return
        settings.setdefault("posters", {})[poster_key] = message.photo[-1].file_id
        settings["poster_exclusions"] = [value for value in settings.get("poster_exclusions", []) if normalize(value) != poster_key]
        save_settings()
        context.user_data.pop("admin_action", None)
        context.user_data.pop("separate_poster_key", None)
        context.user_data.pop("separate_poster_title", None)
        await reply_bold(message, f"✅ Separate poster saved for {title}.")
        return

    if action == "admin_verify_photo":
        settings["verify_post_photo"] = message.photo[-1].file_id
        save_settings()
        context.user_data.pop("admin_action", None)
        await reply_bold(message, "✅ Verification post photo saved.")
        return

    if action == "admin_random_poster":
        collection = context.user_data.setdefault("random_poster_collection", [])
        if len(collection) >= 20:
            await message.reply_text(
                bold_text("⚠️ You already sent 20 posters. Tap Done to save this collection."),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Done", callback_data="random_poster_done", style="success")],
                    [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel", style="danger")],
                ]),
            )
            return
        collection.append(message.photo[-1].file_id)
        await message.reply_text(
            bold_text(f"✅ Poster received ({len(collection)}/20). Send another or tap Done."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data="random_poster_done", style="success")],
                [InlineKeyboardButton("❎ Cancel", callback_data="random_poster_cancel", style="danger")],
            ]),
        )


async def process_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not message.document or not user or not is_admin(user.id):
        return
    action = context.user_data.get("admin_action")
    if action not in {"admin_import", "admin_edit_list"}:
        return
    document = message.document
    if not document.file_name or not document.file_name.lower().endswith(".txt"):
        await reply_bold(message, "Please upload a .txt file.")
        return
    file = await context.bot.get_file(document.file_id)
    content = await file.download_as_bytearray()
    imported_text = content.decode("utf-8", errors="replace")
    anime = load_anime()

    if action == "admin_edit_list":
        lines = [line.strip() for line in imported_text.splitlines() if line.strip()]
        replacement = []
        seen_names = set()
        invalid_lines = []
        for line_number, line in enumerate(lines, start=1):
            entry = parse_anime_entry(line)
            if not entry:
                invalid_lines.append(line_number)
                continue
            key = normalize(entry["search_name"])
            if key and key not in seen_names:
                replacement.append(entry)
                seen_names.add(key)
        if not replacement or invalid_lines:
            bad = ", ".join(str(number) for number in invalid_lines[:15])
            details = f"\nInvalid line(s): {bad}" if bad else ""
            await reply_bold(
                message,
                "❌ The new list was not saved. Every non-empty line must use:\n"
                "BUTTON NAME - URL - GENRE - RATING - SEARCH NAME\n"
                f"Valid entries detected: {len(replacement)}.{details}\n\nYour existing list is unchanged.",
            )
            return
        old_names = {normalize(item["search_name"]) for item in anime}
        save_anime(replacement)
        context.user_data.pop("admin_action", None)
        newly_added = [item["button_name"] for item in replacement if normalize(item["search_name"]) not in old_names]
        await reply_bold(message, f"✅ Anime list replaced successfully.\nSaved entries: {len(replacement)}\nNew titles: {len(newly_added)}")
        if newly_added:
            await notify_new_anime(context, newly_added)
        return

    added_titles = []
    for line in imported_text.splitlines():
        entry = parse_anime_entry(line)
        if not entry:
            continue
        existing_aliases = {alias for item in anime for alias in aliases_for(item["search_name"])}
        if any(alias in existing_aliases for alias in aliases_for(entry["search_name"])):
            continue
        anime.append(entry)
        added_titles.append(entry["button_name"])
    save_anime(anime)
    context.user_data.pop("admin_action", None)
    await reply_bold(message, f"✅ Imported {len(added_titles)} new anime entr(y/ies).")
    if added_titles:
        await notify_new_anime(context, added_titles)

# =========================
# POST-STARTUP RESTORE
# =========================

async def post_init(application: Application):
    """Restore active expiry timers after a Render restart."""
    if application.job_queue:
        for user_id, raw_expiry in settings.get("user_access", {}).items():
            try:
                expiry = float(raw_expiry)
                uid = int(user_id)
            except (TypeError, ValueError):
                continue
            schedule_expiry_job(application, uid, expiry)
        for record in list(settings.get("pending_list_deletions", [])):
            if isinstance(record, dict) and record.get("message_ids"):
                schedule_list_deletion(application, record)

# =========================
# APPLICATION ENTRY POINT
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Render Environment Variables.")

    threading.Thread(target=run_web_server, daemon=True).start()
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", show_list))
    application.add_handler(CommandHandler("trending", trending))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("verifylinks", verifylinks_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("addposters", addposters_command))
    application.add_handler(CommandHandler("cancelposters", cancelposters_command))
    application.add_handler(CommandHandler("removeposters", removeposters_command))
    application.add_handler(CommandHandler("removeallposters", removeallposters_command))
    application.add_handler(CommandHandler("editlist", editlist_command))

    application.add_handler(CallbackQueryHandler(admin_buttons, pattern=r"^(admin_|default_color:|anime_color:|random_poster_|verify_link_|movie_post_)"))
    application.add_handler(CallbackQueryHandler(bot_callback, pattern=r"^(suggest:|request:)"))

    application.add_handler(MessageHandler(filters.ALL, track_group_activity), group=-2)
    application.add_handler(MessageHandler(filters.ALL, process_admin_movie_post), group=-1)
    application.add_handler(MessageHandler(filters.PHOTO, process_admin_photo), group=0)
    application.add_handler(MessageHandler(filters.Document.ALL, process_admin_document), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_text), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_anime), group=1)

    logger.info("Starting ANIFLIX ANIME INDEX BOT TAMIL")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
