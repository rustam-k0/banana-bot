from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from banana_bot.config import AppConfig
from banana_bot.i18n import text
from banana_bot.observability import Metrics


def build_admin_router(config: AppConfig, metrics: Metrics) -> Router:
    router = Router(name="admin")

    @router.message(Command("admin_stats"))
    async def stats(message: Message, state: FSMContext) -> None:
        lang = (await state.get_data()).get("lang", "EN")
        if message.from_user.id not in config.admin_users:
            await message.answer(text(lang, "ADMIN_DENIED")); return
        await message.answer(metrics.render())

    return router
