from app.database import Database
from app.utils import normalize_title

class SearchService:
    def __init__(self, db: Database): self.db = db
    async def search(self, query: str, limit: int = 10):
        normalized = normalize_title(query)
        if not normalized: return []
        return await self.db.search_anime(normalized, limit)
    async def suggestions(self, query: str, limit: int = 5): return await self.search(query, limit)
