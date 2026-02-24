"""
bot.py — Точка входа Banana Bot.

Этот файл:
1. Создаёт бота и диспетчер (aiogram)
2. Подключает middleware для проверки доступа
3. Запускает бота в одном из двух режимов:
   - WEBHOOK — для сервера (Render, Railway, Heroku)
   - POLLING — для локальной разработки на своём компьютере

Режим определяется автоматически:
  - Если в .env указан WEBHOOK_URL → webhook
  - Если WEBHOOK_URL пуст → polling
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message

from config import TELEGRAM_TOKEN, WEBHOOK_URL, PORT, ALLOWED_USERS
from handlers import router

# ─────────────────────────────────────────────────────────
# НАСТРОЙКА ЛОГИРОВАНИЯ
# Все логи выводятся в консоль с временем и уровнем.
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("banana-bot")

# ─────────────────────────────────────────────────────────
# СОЗДАНИЕ БОТА И ДИСПЕТЧЕРА
# parse_mode не задан — каждый handler сам указывает нужный
# ─────────────────────────────────────────────────────────

bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(),  # без parse_mode — указываем в каждом ответе
)
dp = Dispatcher()


# ─────────────────────────────────────────────────────────
# MIDDLEWARE — КОНТРОЛЬ ДОСТУПА
# ─────────────────────────────────────────────────────────

class AuthMiddleware(BaseMiddleware):
    """Пропускает только пользователей из ALLOWED_USERS."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return

        if user.id not in ALLOWED_USERS:
            log.warning(f"⛔ Отказ: {user.id} @{user.username}")
            if isinstance(event, Message):
                try:
                    await event.reply(
                        f"🔒 Доступ закрыт\n\n"
                        f"Бот только для приглашённых.\n"
                        f"Твой ID: {user.id}\n\n"
                        f"Отправь его администратору 🙂"
                    )
                except Exception:
                    pass
            return

        return await handler(event, data)


# Подключаем middleware и роутер с обработчиками
dp.message.middleware(AuthMiddleware())
dp.include_router(router)


# ─────────────────────────────────────────────────────────
# ЗАПУСК БОТА
# ─────────────────────────────────────────────────────────

async def on_startup(bot: Bot, **dp_kwargs):
    """
    Хук, вызываемый aiogram при старте приложения.
    Регистрирует webhook-URL в Telegram.

    ВАЖНО: параметр называется именно `bot` (не bot_instance),
    потому что setup_application передаёт его как keyword-аргумент
    с именем `bot`. Если назвать иначе — получим TypeError.
    """
    await bot.set_webhook(
        f"{WEBHOOK_URL}/webhook",
        drop_pending_updates=True,   # игнорируем старые сообщения
    )
    log.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")


def main():
    """
    Точка входа. Выбирает режим запуска:

    WEBHOOK (для Render и других хостингов):
      1. Создаёт aiohttp-приложение
      2. Регистрирует эндпоинт /webhook для Telegram
      3. setup_application связывает бота с приложением
         (и передаёт `bot` в startup-хуки через kwargs)
      4. Запускает HTTP-сервер на 0.0.0.0:PORT

    POLLING (для локальной разработки):
      1. Удаляет старый webhook (если был)
      2. Запускает бесконечный цикл опроса Telegram API
    """
    if WEBHOOK_URL:
        # ── WEBHOOK-РЕЖИМ (Render.com / Railway / Heroku) ──────
        from aiohttp import web
        from aiogram.webhook.aiohttp_server import (
            SimpleRequestHandler,
            setup_application,
        )

        # Создаём веб-приложение aiohttp
        app = web.Application()

        # Эндпоинт "/" для health-check (Render проверяет, жив ли сервис)
        app.router.add_get("/", lambda _: web.Response(text="Banana Bot 🍌 OK"))

        # Регистрируем обработчик webhook от Telegram на /webhook
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")

        # setup_application связывает aiogram-диспетчер с aiohttp
        # и ПЕРЕДАЁТ bot= в startup-хуки (on_startup получит его)
        setup_application(app, dp, bot=bot)

        # Регистрируем хук для установки webhook
        dp.startup.register(on_startup)

        log.info(f"🚀 WEBHOOK-режим | порт {PORT}")
        web.run_app(app, host="0.0.0.0", port=PORT)

    else:
        # ── POLLING-РЕЖИМ (локальная разработка) ───────────────
        log.info("🚀 POLLING-режим (WEBHOOK_URL не задан)")
        asyncio.run(_polling())


async def _polling():
    """Запуск бота в режиме long polling."""
    try:
        # Удаляем старый webhook, если он был установлен ранее
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("✅ Бот запущен! Жду сообщений… (Ctrl+C для остановки)")
        # Бесконечный цикл опроса Telegram API
        await dp.start_polling(bot)
    finally:
        # Закрываем HTTP-сессию при выходе
        await bot.session.close()


if __name__ == "__main__":
    main()