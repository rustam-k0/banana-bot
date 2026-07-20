from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from banana_bot.observability import Metrics, log_event


class AccessAndMetricsMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: frozenset[int], metrics: Metrics):
        self.allowed_users, self.metrics = allowed_users, metrics

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user and self.allowed_users and user.id not in self.allowed_users:
            log_event("access_denied", user_id=user.id)
            return None
        if user:
            self.metrics.user_activity(user.id)
        return await handler(event, data)
