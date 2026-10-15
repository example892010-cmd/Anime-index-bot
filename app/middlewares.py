from aiogram import BaseMiddleware
class ErrorLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        return await handler(event, data)
