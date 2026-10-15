from app.database import Database
from app.utils import normalize_title

class AnimeService:
    def __init__(self, db: Database): self.db = db
    async def create(self, title: str, aliases: list[str] | None = None, **kwargs) -> int:
        title = title.strip()
        if not title: raise ValueError("Title is required")
        anime_id = await self.db.create_anime(title, normalize_title(title), **kwargs)
        for alias in aliases or []:
            alias = alias.strip()
            if alias and not await self.db.add_alias(anime_id, alias, normalize_title(alias)):
                raise ValueError(f"Duplicate alias: {alias}")
        return anime_id
    async def add_alias(self, anime_id: int, alias: str) -> bool:
        return bool(alias.strip()) and await self.db.add_alias(anime_id, alias.strip(), normalize_title(alias))
    async def get(self, anime_id: int): return await self.db.get_anime(anime_id)
    async def delete(self, anime_id: int) -> bool: return await self.db.delete_anime(anime_id)
