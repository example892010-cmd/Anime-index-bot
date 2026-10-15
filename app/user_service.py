from app.database import Database
class UserService:
    def __init__(self, db: Database): self.db=db
    async def track_interaction(self, user):
        await self.db.upsert_user(user.id, user.first_name or "", user.username)
    async def record_search(self, user, query, matched_id):
        await self.db.record_search(user.id, user.first_name or "", user.username, query, matched_id)
