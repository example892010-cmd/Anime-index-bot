from aiogram.filters import BaseFilter
from aiogram.types import Message

class NonCommandText(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.text and not message.text.startswith("/"))
