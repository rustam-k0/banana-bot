import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from typing import Dict

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOWED_USERS_ENV = os.getenv("ALLOWED_USERS", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

# Парсим ID разрешенных пользователей
ALLOWED_USERS = set()
for u in ALLOWED_USERS_ENV.split(","):
    if u.strip().isdigit():
        ALLOWED_USERS.add(int(u.strip()))

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    logging.error("Не найден TELEGRAM_BOT_TOKEN или GOOGLE_API_KEY в .env")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
gemini_client = genai.Client(api_key=GOOGLE_API_KEY, http_options={"api_version": "v1alpha"})

# ==========================================
# КОНСТАНТЫ И СОСТОЯНИЯ
# ==========================================
BTN_ART = "🎨 Сгенерировать картинку"
BTN_EDIT = "🪄 Изменить фото"
BTN_HELP = "ℹ️ Помощь"
BTN_MODE_PRO = "💎 Режим: PRO (Детальный)"
BTN_MODE_FLASH = "🚀 Режим: FLASH (Быстрый)"

# user_id -> mode ("PRO" или "FLASH")
user_modes: Dict[int, str] = {}
# user_id -> action ("WAITING_ART" | "WAITING_EDIT_PHOTO" | "WAITING_EDIT_PROMPT" | None)
user_actions: Dict[int, str] = {}
# user_id -> bytes (Временное хранилище фото для изменения)
user_edit_images: Dict[int, bytes] = {}

MODES = {
    "PRO": ["gemini-3-pro-image-preview", "gemini-3.1-pro-preview"],
    "FLASH": ["gemini-2.5-flash-image", "gemini-3-flash"]
}

def get_user_mode(user_id: int) -> str:
    return user_modes.get(user_id, "FLASH")  # По умолчанию быстрый режим

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    current_mode = get_user_mode(user_id)
    # Показываем кнопку ДРУГОГО режима, чтобы пользователь мог на неё нажать для переключения
    mode_btn = BTN_MODE_PRO if current_mode == "FLASH" else BTN_MODE_FLASH
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ART), KeyboardButton(text=BTN_EDIT)],
            [KeyboardButton(text=mode_btn)],
            [KeyboardButton(text=BTN_HELP)]
        ],
        resize_keyboard=True
    )
    return keyboard

# ==========================================
# ДОСТУП
# ==========================================
@dp.message.outer_middleware()
async def access_control_middleware(handler, event: Message, data: dict):
    if ALLOWED_USERS and event.from_user.id not in ALLOWED_USERS:
        logging.warning(f"Доступ запрещен: {event.from_user.id}")
        return
    return await handler(event, data)

# ==========================================
# ОБРАБОТЧИКИ КНОПОК
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_actions.pop(message.from_user.id, None)
    user_edit_images.pop(message.from_user.id, None)
    text = (
        "👋 Привет! Я — бот-робот для работы с изображениями.\n\n"
        "Выберите действие на клавиатуре ниже:"
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == BTN_ART)
async def cmd_art(message: Message):
    user_actions[message.from_user.id] = "WAITING_ART"
    text = (
        "С удовольствием! Я готов создать для вас изображение.\n\n"
        "**Что бы вы хотели увидеть на картинке?**\n\n"
        "Опишите вашу идею как можно подробнее (объекты, стиль, атмосфера, цвета). Чем детальнее запрос, тем лучше результат!"
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == BTN_EDIT)
async def cmd_edit(message: Message):
    user_actions[message.from_user.id] = "WAITING_EDIT_PHOTO"
    await message.answer("🪄 Отправьте мне **фотографию**, которую нужно изменить.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == BTN_HELP)
async def cmd_help(message: Message):
    user_actions.pop(message.from_user.id, None)
    text = (
        "ℹ️ **Справка по боту**\n\n"
        "• **Сгенерировать картинку** — Нажмите кнопку, затем отправьте текстовое описание, и я нарисую изображение.\n"
        "• **Изменить фото** — Нажмите кнопку, отправьте фото, затем текст с инструкциями, и я внесу изменения.\n"
        "• **Смена режима** — Нажмите на кнопку с ракетой/алмазом, чтобы переключаться между быстрым (FLASH) и детальным (PRO) режимами."
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == BTN_MODE_PRO)
async def cmd_set_pro(message: Message):
    user_id = message.from_user.id
    user_modes[user_id] = "PRO"
    await message.answer("💎 Режим PRO активирован! Качество улучшено.", reply_markup=get_main_keyboard(user_id))

@dp.message(F.text == BTN_MODE_FLASH)
async def cmd_set_flash(message: Message):
    user_id = message.from_user.id
    user_modes[user_id] = "FLASH"
    await message.answer("Принято! ⚡ FLASH-режим. Максимальная скорость. Жду задачу.", reply_markup=get_main_keyboard(user_id))

# ==========================================
# ФУНКЦИИ ГЕНЕРАЦИИ ЧЕРЕЗ GEMINI
# ==========================================
async def generate_image_cascade(prompt: str, mode: str, message: Message) -> bytes | None:
    models = MODES.get(mode, MODES["FLASH"])
    for model_name in models:
        try:
            response = await gemini_client.aio.models.generate_content(
                model=model_name,
                contents=[prompt]
            )
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            inline_data = getattr(part, 'inline_data', None)
                            if inline_data:
                                data = getattr(inline_data, 'data', None)
                                if data:
                                    return data
        except Exception as e:
            logging.error(f"Ошибка с моделью рисования {model_name}: {e}")
            continue
    await message.answer("❌ Ошибка генерации: сервис создания картинок временно перегружен или недоступен.")
    return None

async def edit_image(image_bytes: bytes, prompt: str, mode: str, message: Message) -> bytes | None:
    try:
        models = MODES.get(mode, MODES["FLASH"])
        for model_name in models:
            try:
                contents = [
                    genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    prompt
                ]
                response = await gemini_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                if response.candidates:
                    for candidate in response.candidates:
                        if candidate.content and candidate.content.parts:
                            for part in candidate.content.parts:
                                inline_data = getattr(part, 'inline_data', None)
                                if inline_data:
                                    data = getattr(inline_data, 'data', None)
                                    if data:
                                        return data
            except Exception as e:
                logging.error(f"Ошибка с моделью изменения {model_name}: {e}")
                continue
        await message.answer("❌ Ошибка при изменении картинки. Временно недоступно.")
        return None
    except Exception as e:
        logging.error(f"Глобальная ошибка при изменении изображения: {e}")
        await message.answer("❌ Ошибка при изменении картинки. Временно недоступно.")
        return None

async def transcribe_audio(audio_bytes: bytes, mode: str) -> str | None:
    try:
        contents = [
            genai_types.Part.from_bytes(data=audio_bytes, mime_type='audio/ogg'),
            "Транскрибируй это голосовое сообщение в текст. Выведи только распознанный текст без лишних слов."
        ]
        models = MODES.get(mode, MODES["FLASH"])
        for model_name in models:
            try:
                response = await gemini_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                if response.text:
                    return response.text.strip()
            except Exception as e:
                logging.error(f"Ошибка с текстовой моделью {model_name}: {e}")
                continue
        return None
    except Exception as e:
        logging.error(f"Глобальная ошибка при распознавании голоса: {e}")
        return None

# ==========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ
# ==========================================
async def process_user_text_input(text: str, message: Message, bot: Bot):
    """Общая логика обработки текста или расшифрованного голоса"""
    user_id = message.from_user.id
    action = user_actions.get(user_id)
    mode = get_user_mode(user_id)
    
    if action == "WAITING_ART":
        msg = await message.answer("⏳ Рисую...")
        image_bytes = await generate_image_cascade(text, mode, message)
        if image_bytes:
            await message.answer_photo(types.BufferedInputFile(image_bytes, filename="art.jpg"))
            user_actions.pop(user_id, None)
        await msg.delete()
        
    elif action == "WAITING_EDIT_PROMPT":
        image_bytes = user_edit_images.get(user_id)
        if not image_bytes:
            await message.answer("⚠️ Ошибка: фотография не найдена. Попробуйте нажать кнопку '🪄 Изменить фото' заново.")
            user_actions.pop(user_id, None)
            return

        msg = await message.answer("⏳ Волшебство в процессе (изменяю картинку)...")
        edited_image_bytes = await edit_image(image_bytes, text, mode, message)
        if edited_image_bytes:
            await message.answer_photo(types.BufferedInputFile(edited_image_bytes, filename="edited.jpg"))
            user_actions.pop(user_id, None)
            user_edit_images.pop(user_id, None)
        await msg.delete()

    elif action == "WAITING_EDIT_PHOTO":
        await message.answer("⚠️ Я жду от вас **фотографию**, а не текст. Отправьте картинку!")

    else:
        # Если статус не задан (бот возвращает хардкод загулшку)
        await message.answer("👆 Пожалуйста, выберите действие на клавиатуре ниже (нарисовать картинку или изменить фото).")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot):
    await process_user_text_input(message.text, message, bot)

@dp.message(F.voice)
async def handle_user_voice(message: Message, bot: Bot):
    user_id = message.from_user.id
    action = user_actions.get(user_id)

    if action == "WAITING_EDIT_PHOTO":
        await message.answer("⚠️ Я жду от вас **фотографию**, а не голосовое сообщение. Отправьте картинку!")
        return
        
    if not action:
        await message.answer("👆 Сначала выберите действие на клавиатуре ниже (нарисовать картинку или изменить фото).")
        return

    msg = await message.answer("⏳ Слушаю...")
    
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    downloaded_file = await bot.download_file(file_path)
    audio_bytes = downloaded_file.read()

    mode = get_user_mode(user_id)
    text = await transcribe_audio(audio_bytes, mode)
    await msg.delete()

    if text:
        # Отобразим пользователю, как мы поняли его голосовое, для ясности (но это опционально, можно сразу передать дальше)
        await message.answer(f"🎙 *Распознано:* {text}", parse_mode=ParseMode.MARKDOWN)
        await process_user_text_input(text, message, bot)
    else:
        await message.answer("❌ Извините, не удалось распознать голосовое сообщение.")

@dp.message(F.photo)
async def handle_user_photo(message: Message, bot: Bot):
    user_id = message.from_user.id
    action = user_actions.get(user_id)
    
    if action == "WAITING_EDIT_PHOTO":
        msg = await message.answer("Загружаю фото...")
        
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        downloaded_file = await bot.download_file(file_path)
        
        user_edit_images[user_id] = downloaded_file.read()
        user_actions[user_id] = "WAITING_EDIT_PROMPT"
        
        await msg.delete()
        await message.answer("📸 Фото получено! Теперь отправьте текстом (или голосовым), что именно нужно на нём изменить.")
        
    elif action == "WAITING_EDIT_PROMPT":
         await message.answer("⚠️ Фото уже получено. Отправьте **текст**, описывающий необходимые изменения.")

    elif action == "WAITING_ART":
        await message.answer("⚠️ Для генерации новой картинки нужен **текст** (описание), а не фото. Отправьте словесное описание для Арта.")
        
    else:
        await message.answer("👆 Выберите действие '🪄 Изменить фото' на клавиатуре перед отправкой фотографий.")

@dp.message()
async def handle_other_media(message: Message):
    await message.answer("⚠️ Я работаю только с текстом, голосовыми и обычными фотографиями. Пожалуйста, используйте кнопки.")


# ==========================================
# ТОЧКА ВХОДА И ЗАПУСК (MAIN)
# ==========================================
async def main():
    if WEBHOOK_URL:
        logging.info(f"Запуск Webhook на порту {PORT}")
        app = web.Application()
        # secret_token допускает только A-Z, a-z, 0-9, _ и -
        webhook_secret = TELEGRAM_BOT_TOKEN.replace(":", "")
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=webhook_secret
        )
        webhook_requests_handler.register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook", secret_token=webhook_secret)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        await site.start()
        
        while True:
            await asyncio.sleep(3600)
    else:
        logging.info("Запуск локального Polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")
