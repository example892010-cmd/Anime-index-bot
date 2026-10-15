from aiogram import Bot
from app.database import Database

class ForceSubService:
    def __init__(self, db: Database): self.db=db
    async def enabled(self):
        return await self.db.get_setting("force_sub_enabled", "0") == "1" and await self.db.list_force_sub(True)
    async def missing_channels(self, bot: Bot, user_id: int) -> tuple[list, list[str]]:
        channels = await self.db.list_force_sub(True); missing=[]; errors=[]
        for ch in channels:
            try:
                member = await bot.get_chat_member(chat_id=ch["channel_id"], user_id=user_id)
                if member.status not in {"creator", "administrator", "member"} and not (member.status == "restricted" and getattr(member, "is_member", False)):
                    missing.append(ch)
            except Exception as exc:
                errors.append(f"{ch['display_name']}: {type(exc).__name__}")
                missing.append(ch)
        return missing, errors
    async def allowed(self, bot: Bot, user_id: int) -> bool:
        if not await self.enabled(): return True
        missing, _ = await self.missing_channels(bot, user_id)
        return not missing
