from app.database import Database
from app.utils import valid_url
class LinkService:
    def __init__(self, db: Database): self.db=db
    async def add(self, anime_id: int, display_title: str, link_type: str, url: str, **kwargs):
        if not valid_url(url): raise ValueError("Only valid http(s) URLs are accepted")
        if not await self.db.get_anime(anime_id): raise ValueError("Anime ID does not exist or is inactive")
        if link_type not in {"public_channel","private_channel","telegram_post","external"}: raise ValueError("Unsupported link type")
        return await self.db.add_link(anime_id, display_title, link_type, url, **kwargs)
