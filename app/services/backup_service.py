from __future__ import annotations
import sqlite3
from pathlib import Path
import shutil
from app.database import Database

class BackupService:
    def __init__(self, db: Database):
        self.db = db

    async def create_backup(self, destination: str) -> str:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not self.db.conn:
            raise RuntimeError("Database is not connected")
        # aiosqlite connection exposes the underlying SQLite connection; use
        # the SQL backup API on a separate read connection to avoid copying WAL
        # files by hand. SQLite's backup API gives a consistent snapshot.
        source = sqlite3.connect(self.db.path)
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return str(dest)

    @staticmethod
    def validate(path: str) -> tuple[bool, str]:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"users", "anime", "search_logs", "bot_settings", "force_sub_channels"}
            if integrity != "ok":
                return False, str(integrity)
            missing = required - tables
            if missing:
                return False, "Missing required tables: " + ", ".join(sorted(missing))
            return True, "ok"
        except (OSError, sqlite3.DatabaseError) as exc:
            return False, str(exc)
        finally:
            if conn is not None:
                conn.close()

    async def restore(self, source_path: str) -> None:
        valid, reason = self.validate(source_path)
        if not valid:
            raise ValueError(f"Invalid backup: {reason}")
        destination = Path(self.db.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        safety = destination.with_suffix(destination.suffix + ".pre-restore")
        # Make a rollback copy before replacing the live database.
        await self.create_backup(str(safety))
        await self.db.close()
        try:
            shutil.copy2(source_path, destination)
            await self.db.connect()
        except Exception:
            if safety.exists():
                shutil.copy2(safety, destination)
            await self.db.connect()
            raise
        finally:
            safety.unlink(missing_ok=True)
