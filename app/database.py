from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA_VERSION = 1

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class Database:
    def __init__(self, path: str):
        self.path = str(Path(path).expanduser())
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> "Database":
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.init_schema()
        return self

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def init_schema(self) -> None:
        assert self.conn
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO schema_meta(key,value) VALUES ('schema_version','1');
        CREATE TABLE IF NOT EXISTS users (
          user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL DEFAULT '', username TEXT,
          first_interaction TEXT NOT NULL, last_interaction TEXT NOT NULL,
          total_searches INTEGER NOT NULL DEFAULT 0, successful_searches INTEGER NOT NULL DEFAULT 0,
          unsuccessful_searches INTEGER NOT NULL DEFAULT 0, last_search_at TEXT, last_searched_title TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
          user_id INTEGER PRIMARY KEY, added_by INTEGER NOT NULL, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS anime (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, normalized_title TEXT NOT NULL UNIQUE,
          description TEXT NOT NULL DEFAULT '', poster TEXT NOT NULL DEFAULT '', anime_type TEXT NOT NULL DEFAULT 'series',
          audio_language TEXT NOT NULL DEFAULT 'Tamil', season_info TEXT NOT NULL DEFAULT '', episode_info TEXT NOT NULL DEFAULT '',
          genres TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS anime_aliases (
          id INTEGER PRIMARY KEY AUTOINCREMENT, anime_id INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
          alias TEXT NOT NULL, normalized_alias TEXT NOT NULL, UNIQUE(anime_id, normalized_alias)
        );
        CREATE INDEX IF NOT EXISTS idx_alias_normalized ON anime_aliases(normalized_alias);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_global_unique ON anime_aliases(normalized_alias);
        CREATE TABLE IF NOT EXISTS anime_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT, anime_id INTEGER NOT NULL REFERENCES anime(id) ON DELETE CASCADE,
          display_title TEXT NOT NULL, link_type TEXT NOT NULL, url TEXT NOT NULL, season_number INTEGER,
          episode_number INTEGER, quality TEXT, language TEXT, display_order INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_links_anime_active ON anime_links(anime_id, active, display_order);
        CREATE TABLE IF NOT EXISTS force_sub_channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          username TEXT, channel_type TEXT NOT NULL DEFAULT 'public', invite_url TEXT, join_label TEXT NOT NULL DEFAULT 'Join channel',
          enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL, action TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS search_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
          query TEXT NOT NULL, matched_anime_id INTEGER REFERENCES anime(id) ON DELETE SET NULL,
          success INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_search_logs_recent ON search_logs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_search_logs_user ON search_logs(user_id);
        """)
        async with self.conn.execute("PRAGMA table_info(anime_links)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "button_color" not in columns:
            await self.conn.execute("ALTER TABLE anime_links ADD COLUMN button_color TEXT NOT NULL DEFAULT 'green'")
        defaults = {
            "welcome_message": "👋 Hi {first_name}!\n\nWelcome to ANIFLIX Anime Search Bot ❤️\n\n🔎 Type and send the anime name you want to search for.\n\n✨ Example: Naruto\n\n📚 Search your favourite anime and discover the available links!",
            "start_image": "", "updates_channel": "", "support_link": "", "force_sub_enabled": "0",
            "force_sub_message": "🔒 JOIN REQUIRED CHANNELS\n\nPlease join all required channels below to continue using ANIFLIX Anime Search Bot.",
            "maintenance_mode": "0", "bot_display_name": "ANIFLIX NETWORK TAMIL", "page_size": "5",
        }
        for key, value in defaults.items():
            await self.conn.execute("INSERT OR IGNORE INTO bot_settings(key,value) VALUES (?,?)", (key, value))
        await self.conn.commit()

    async def _fetchone(self, sql: str, args: tuple = ()) -> aiosqlite.Row | None:
        assert self.conn
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, args: tuple = ()) -> list[aiosqlite.Row]:
        assert self.conn
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchall()

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self._fetchone("SELECT value FROM bot_settings WHERE key=?", (key,))
        return str(row[0]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        assert self.conn
        async with self._lock:
            await self.conn.execute("INSERT INTO bot_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            await self.conn.commit()

    async def log_audit(self, actor_id: int, action: str, details: str = "") -> None:
        assert self.conn
        await self.conn.execute("INSERT INTO audit_logs(actor_id,action,details,created_at) VALUES (?,?,?,?)", (actor_id, action, details, utcnow()))
        await self.conn.commit()

    async def is_admin(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id: return True
        return bool(await self._fetchone("SELECT 1 FROM admins WHERE user_id=?", (user_id,)))

    async def add_admin(self, user_id: int, actor_id: int) -> None:
        assert self.conn
        await self.conn.execute("INSERT OR IGNORE INTO admins(user_id,added_by,added_at) VALUES (?,?,?)", (user_id, actor_id, utcnow()))
        await self.conn.commit()

    async def remove_admin(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id: return False
        assert self.conn
        cur = await self.conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_admins(self) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM admins ORDER BY added_at")

    async def upsert_user(self, user_id: int, first_name: str, username: str | None) -> None:
        assert self.conn
        now = utcnow()
        await self.conn.execute("""INSERT INTO users(user_id,first_name,username,first_interaction,last_interaction)
          VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name, username=excluded.username, last_interaction=excluded.last_interaction""", (user_id, first_name[:200], username, now, now))
        await self.conn.commit()

    async def record_search(self, user_id: int, first_name: str, username: str | None, query: str, matched_id: int | None) -> None:
        assert self.conn
        now = utcnow(); success = 1 if matched_id else 0
        async with self._lock:
            await self.conn.execute("""INSERT INTO users(user_id,first_name,username,first_interaction,last_interaction,total_searches,successful_searches,unsuccessful_searches,last_search_at,last_searched_title)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name, username=excluded.username,
              last_interaction=excluded.last_interaction,total_searches=users.total_searches+1,successful_searches=users.successful_searches+excluded.successful_searches,
              unsuccessful_searches=users.unsuccessful_searches+excluded.unsuccessful_searches,last_search_at=excluded.last_search_at,last_searched_title=excluded.last_searched_title""",
              (user_id, first_name[:200], username, now, now, 1, success, 1-success, now, query[:500]))
            await self.conn.execute("INSERT INTO search_logs(user_id,query,matched_anime_id,success,created_at) VALUES (?,?,?,?,?)", (user_id, query[:500], matched_id, success, now))
            await self.conn.commit()

    async def get_search_users(self, limit: int, offset: int) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM users WHERE total_searches>0 ORDER BY last_search_at DESC LIMIT ? OFFSET ?", (limit, offset))

    async def count_search_users(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) FROM users WHERE total_searches>0")
        return int(row[0]) if row else 0

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def recent_searches(self, limit: int = 20) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT s.*, u.first_name, u.username, a.title FROM search_logs s JOIN users u ON u.user_id=s.user_id LEFT JOIN anime a ON a.id=s.matched_anime_id ORDER BY s.created_at DESC LIMIT ?", (limit,))

    async def stats(self) -> dict[str, int]:
        names = {"registered_users":"SELECT COUNT(*) FROM users", "searching_users":"SELECT COUNT(*) FROM users WHERE total_searches>0", "total_searches":"SELECT COALESCE(SUM(total_searches),0) FROM users", "successful_searches":"SELECT COALESCE(SUM(successful_searches),0) FROM users", "unsuccessful_searches":"SELECT COALESCE(SUM(unsuccessful_searches),0) FROM users", "anime":"SELECT COUNT(*) FROM anime", "aliases":"SELECT COUNT(*) FROM anime_aliases", "links":"SELECT COUNT(*) FROM anime_links WHERE active=1", "force_sub":"SELECT COUNT(*) FROM force_sub_channels WHERE enabled=1", "admins":"SELECT COUNT(*) FROM admins"}
        out: dict[str,int] = {}
        for k, sql in names.items():
            row = await self._fetchone(sql); out[k] = int(row[0]) if row else 0
        return out

    async def create_anime(self, title: str, normalized: str, **kwargs: Any) -> int:
        assert self.conn
        now = utcnow()
        try:
            cur = await self.conn.execute("INSERT INTO anime(title,normalized_title,description,poster,anime_type,audio_language,season_info,episode_info,genres,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (title, normalized, kwargs.get("description", ""), kwargs.get("poster", ""), kwargs.get("anime_type", "series"), kwargs.get("audio_language", "Tamil"), kwargs.get("season_info", ""), kwargs.get("episode_info", ""), kwargs.get("genres", ""), now, now))
            await self.conn.commit(); return int(cur.lastrowid)
        except aiosqlite.IntegrityError as exc:
            await self.conn.rollback(); raise ValueError("Anime title already exists") from exc

    async def update_anime(self, anime_id: int, **fields: Any) -> bool:
        allowed = {"title","normalized_title","description","poster","anime_type","audio_language","season_info","episode_info","genres","active"}
        fields = {k:v for k,v in fields.items() if k in allowed}
        if not fields: return False
        fields["updated_at"] = utcnow()
        assert self.conn
        sets = ",".join(f"{k}=?" for k in fields)
        cur = await self.conn.execute(f"UPDATE anime SET {sets} WHERE id=?", tuple(fields.values()) + (anime_id,)); await self.conn.commit(); return cur.rowcount > 0

    async def delete_anime(self, anime_id: int) -> bool:
        assert self.conn
        cur = await self.conn.execute("DELETE FROM anime WHERE id=?", (anime_id,)); await self.conn.commit(); return cur.rowcount > 0

    async def add_alias(self, anime_id: int, alias: str, normalized: str) -> bool:
        assert self.conn
        try:
            await self.conn.execute("INSERT INTO anime_aliases(anime_id,alias,normalized_alias) VALUES (?,?,?)", (anime_id, alias, normalized)); await self.conn.commit(); return True
        except aiosqlite.IntegrityError: await self.conn.rollback(); return False

    async def get_anime(self, anime_id: int) -> aiosqlite.Row | None:
        return await self._fetchone("SELECT * FROM anime WHERE id=? AND active=1", (anime_id,))

    async def get_anime_aliases(self, anime_id: int) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM anime_aliases WHERE anime_id=? ORDER BY alias", (anime_id,))

    async def search_anime(self, normalized: str, limit: int = 10) -> list[aiosqlite.Row]:
        # Exact title/alias first, then prefix, then contained phrases. Parameterized and indexed fields.
        return await self._fetchall("""SELECT a.*, CASE WHEN a.normalized_title=? THEN 0 WHEN EXISTS(SELECT 1 FROM anime_aliases x WHERE x.anime_id=a.id AND x.normalized_alias=?) THEN 1 WHEN a.normalized_title LIKE ? THEN 2 WHEN EXISTS(SELECT 1 FROM anime_aliases x WHERE x.anime_id=a.id AND x.normalized_alias LIKE ?) THEN 3 ELSE 4 END rank FROM anime a WHERE a.active=1 AND (a.normalized_title=? OR a.normalized_title LIKE ? OR a.normalized_title LIKE ? OR EXISTS(SELECT 1 FROM anime_aliases x WHERE x.anime_id=a.id AND (x.normalized_alias=? OR x.normalized_alias LIKE ? OR x.normalized_alias LIKE ?))) ORDER BY rank, length(a.title), a.title LIMIT ?""", (normalized, normalized, normalized+"%", normalized+"%", normalized, "%"+normalized+"%", normalized+"%", normalized, "%"+normalized+"%", normalized+"%", limit))

    async def links_for_anime(self, anime_id: int) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM anime_links WHERE anime_id=? AND active=1 ORDER BY display_order,id", (anime_id,))

    async def add_link(self, anime_id: int, display_title: str, link_type: str, url: str, **kwargs: Any) -> int:
        assert self.conn
        color = kwargs.get("button_color", "green")
        if color not in {"green", "blue", "red"}:
            raise ValueError("Unsupported button color")
        cur = await self.conn.execute("INSERT INTO anime_links(anime_id,display_title,link_type,url,season_number,episode_number,quality,language,display_order,active,button_color,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (anime_id, display_title, link_type, url, kwargs.get("season_number"), kwargs.get("episode_number"), kwargs.get("quality"), kwargs.get("language"), kwargs.get("display_order",0), kwargs.get("active",1), color, utcnow()))
        await self.conn.commit(); return int(cur.lastrowid)

    async def upsert_force_sub(self, channel_id: str, display_name: str, **kwargs: Any) -> int:
        assert self.conn; now = utcnow()
        cur = await self.conn.execute("""INSERT INTO force_sub_channels(channel_id,display_name,username,channel_type,invite_url,join_label,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(channel_id) DO UPDATE SET display_name=excluded.display_name,username=excluded.username,channel_type=excluded.channel_type,invite_url=excluded.invite_url,join_label=excluded.join_label,enabled=excluded.enabled,updated_at=excluded.updated_at""", (str(channel_id),display_name,kwargs.get("username"),kwargs.get("channel_type","public"),kwargs.get("invite_url"),kwargs.get("join_label","Join channel"),kwargs.get("enabled",1),now,now)); await self.conn.commit(); return int(cur.lastrowid or 0)

    async def list_force_sub(self, enabled_only: bool = False) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM force_sub_channels WHERE enabled=1 ORDER BY id" if enabled_only else "SELECT * FROM force_sub_channels ORDER BY id")

    async def remove_force_sub(self, channel_id: str) -> bool:
        assert self.conn; cur = await self.conn.execute("DELETE FROM force_sub_channels WHERE channel_id=?", (str(channel_id),)); await self.conn.commit(); return cur.rowcount > 0

    async def set_force_sub_channel_enabled(self, channel_id: str, enabled: bool) -> bool:
        assert self.conn
        cur = await self.conn.execute(
            "UPDATE force_sub_channels SET enabled=?, updated_at=? WHERE channel_id=?",
            (1 if enabled else 0, utcnow(), str(channel_id)))
        await self.conn.commit()
        return cur.rowcount > 0

    async def update_force_sub_invite(self, channel_id: str, invite_url: str) -> bool:
        assert self.conn
        cur = await self.conn.execute(
            "UPDATE force_sub_channels SET invite_url=?, channel_type='private', updated_at=? WHERE channel_id=?",
            (invite_url, utcnow(), str(channel_id)))
        await self.conn.commit()
        return cur.rowcount > 0

    async def list_links(self, anime_id: int) -> list[aiosqlite.Row]:
        return await self._fetchall("SELECT * FROM anime_links WHERE anime_id=? ORDER BY display_order,id", (anime_id,))

    async def delete_link(self, link_id: int) -> bool:
        assert self.conn
        cur = await self.conn.execute("DELETE FROM anime_links WHERE id=?", (link_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def disable_link(self, link_id: int) -> bool:
        assert self.conn
        cur = await self.conn.execute("UPDATE anime_links SET active=0 WHERE id=?", (link_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_alias(self, alias_id: int) -> bool:
        assert self.conn
        cur = await self.conn.execute("DELETE FROM anime_aliases WHERE id=?", (alias_id,))
        await self.conn.commit()
        return cur.rowcount > 0
