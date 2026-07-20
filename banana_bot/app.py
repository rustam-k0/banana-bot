from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
import redis.asyncio as redis

from banana_bot.adapters.google import GoogleAdapter
from banana_bot.adapters.openai import OpenAIAdapter
from banana_bot.adapters.xai import XAIAdapter
from banana_bot.config import AppConfig, load_config
from banana_bot.http import AsyncHTTPClient
from banana_bot.memory import ConversationMemory
from banana_bot.middleware import AccessAndMetricsMiddleware
from banana_bot.observability import Metrics, log_event
from banana_bot.routers import build_admin_router, build_common_router, build_media_router, build_text_router
from banana_bot.services.ai import AIService
from banana_bot import __version__


async def healthcheck(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "banana-bot", "version": __version__})


async def start_health_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", healthcheck)
    app.router.add_get("/healthz", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner


def build_dispatcher(config: AppConfig, ai: AIService, memory: ConversationMemory, metrics: Metrics, storage=None) -> Dispatcher:
    dispatcher = Dispatcher(storage=storage or MemoryStorage())
    dispatcher.message.outer_middleware(AccessAndMetricsMiddleware(config.allowed_users, metrics))
    dispatcher.callback_query.outer_middleware(AccessAndMetricsMiddleware(config.allowed_users, metrics))
    dispatcher.include_router(build_admin_router(config, metrics))
    dispatcher.include_router(build_common_router(memory))
    dispatcher.include_router(build_text_router(ai))
    dispatcher.include_router(build_media_router(ai))
    return dispatcher


async def run(config: AppConfig) -> None:
    config.validate()
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    client = AsyncHTTPClient(config.request_timeout_seconds, config.connect_timeout_seconds, config.http_retries, config.rate_limit_per_minute)
    adapters = {}
    if config.openai_api_key: adapters["openai"] = OpenAIAdapter(client, config.openai_api_key)
    if config.xai_api_key: adapters["xai"] = XAIAdapter(client, config.xai_api_key)
    if config.google_api_key: adapters["google"] = GoogleAdapter(client, config.google_api_key)
    memory, metrics = ConversationMemory(config.memory_messages), Metrics()
    ai = AIService(config, adapters, memory, metrics)
    redis_client = None
    storage = MemoryStorage()
    if config.redis_url:
        candidate = redis.from_url(config.redis_url, decode_responses=False)
        try:
            await candidate.ping()
            redis_client = candidate
            storage = RedisStorage(redis=redis_client)
            log_event("redis_ready")
        except Exception as exc:
            log_event("redis_unavailable", error_type=type(exc).__name__)
            await candidate.aclose()
    dispatcher = build_dispatcher(config, ai, memory, metrics, storage)
    bot = Bot(config.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log_event("startup", webhook=bool(config.webhook_url), providers=sorted(adapters))
    runner: web.AppRunner | None = None
    try:
        if config.webhook_url:
            app = web.Application()
            app.router.add_get("/", healthcheck)
            app.router.add_get("/healthz", healthcheck)
            secret = config.telegram_bot_token.replace(":", "")
            SimpleRequestHandler(dispatcher=dispatcher, bot=bot, secret_token=secret).register(app, path="/webhook")
            setup_application(app, dispatcher, bot=bot)
            await bot.set_webhook(f"{config.webhook_url.rstrip('/')}/webhook", secret_token=secret)
            runner = web.AppRunner(app); await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", config.port).start()
            await asyncio.Event().wait()
        else:
            # Render web services require an open port even when Telegram uses polling.
            runner = await start_health_server(config.port)
            await bot.delete_webhook(drop_pending_updates=True)
            await dispatcher.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        await client.close()
        await bot.session.close()
        if redis_client: await redis_client.aclose()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config(validate=True)
    except ValueError as exc:
        logging.error("Startup configuration is invalid: %s", exc)
        sys.exit(1)
    asyncio.run(run(config))
