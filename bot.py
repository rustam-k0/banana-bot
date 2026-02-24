import asyncio
import logging
import os
import sys
import html
import re

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError
from typing import Dict, Optional
import redis.asyncio as redis

# Очистка логеров сторонних библиотек от лишнего спама
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("google.api_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Подгрузка переменных окружения
load_dotenv()

# Чтение основных настроек
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOWED_USERS_ENV = os.getenv("ALLOWED_USERS", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))
REDIS_URL = os.getenv("REDIS_URL")

# Формирование множества ID пользователей, которым разрешен доступ к боту
ALLOWED_USERS = set()
for u in ALLOWED_USERS_ENV.split(","):
    if u.strip().isdigit():
        ALLOWED_USERS.add(int(u.strip()))

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    logging.error("Не найден TELEGRAM_BOT_TOKEN или GOOGLE_API_KEY в .env")
    sys.exit(1)

# Инициализация объектов Aiogram с указанием формата разметки по умолчанию
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Инициализация клиента Google Gemini
gemini_client = genai.Client(api_key=GOOGLE_API_KEY, http_options={"api_version": "v1alpha"})

# ==========================================
# ИНИЦИАЛИЗАЦИЯ ХРАНИЛИЩА СОСТОЯНИЙ (FSM)
# ==========================================
# Если указан URL Redis, используем его для хранения состояний и данных пользователей
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        storage = RedisStorage(redis=redis_client)
        logging.info("Redis успешно подключен для хранения состояний FSM.")
    except Exception as e:
        logging.error(f"Ошибка при подключении к Redis: {e}")
        redis_client = None
        storage = MemoryStorage()
        logging.info("Откат: Используется in-memory хранилище FSM (Внимание: данные очищаются при перезапуске).")
else:
    redis_client = None
    storage = MemoryStorage()
    logging.info("REDIS_URL не найден, используется in-memory хранилище FSM (Внимание: данные очищаются при перезапуске).")

dp = Dispatcher(storage=storage)


# ==========================================
# КОНСТАНТЫ И СОСТОЯНИЯ FSM
# ==========================================

# Тексты кнопок главного меню
BTN_GENERATE_IMAGE = "🎨 Сгенерировать фото"
BTN_EDIT_IMAGE = "🪄 Изменить фото"
BTN_HELP = "💡 Справка"
BTN_MODE_PRO = "💎 Детально (PRO)"
BTN_MODE_FLASH = "⚡️ Быстро (FLASH)"

class BotStates(StatesGroup):
    """Возможные шаги текущего сеанса пользователя"""
    WAITING_FOR_IMAGE_PROMPT = State()   # Бот ждет описание фото для генерации с нуля
    WAITING_FOR_PHOTO_TO_EDIT = State()  # Бот ждет саму фотографию для изменения
    WAITING_FOR_EDIT_PROMPT = State()    # Бот получил фото и ждет текстовую инструкцию (что изменить)

# Конфигурация используемых моделей для различных задач в зависимости от выбранного режима
IMAGE_GEN_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-2.5-flash-image"]
}

IMAGE_EDIT_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-2.5-flash-image"]
}

TEXT_AUDIO_MODELS = {
    "PRO": ["gemini-3-flash-preview"],
    "FLASH": ["gemini-3-flash-preview"]
}


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def format_html_response(text: str) -> str:
    """Утилитная функция: экранирует текст пользователя и конвертирует базовый Markdown в HTML-теги для Telegram"""
    text = html.escape(text, quote=False)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text

async def get_main_keyboard(state: FSMContext) -> ReplyKeyboardMarkup:
    """Динамическое формирование главной клавиатуры. Подстраивает кнопку режима на противоположную текущему."""
    data = await state.get_data()
    current_mode = data.get("mode", "FLASH")
    mode_btn = BTN_MODE_PRO if current_mode == "FLASH" else BTN_MODE_FLASH
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_GENERATE_IMAGE), KeyboardButton(text=BTN_EDIT_IMAGE)],
            [KeyboardButton(text=mode_btn)],
            [KeyboardButton(text=BTN_HELP)]
        ],
        resize_keyboard=True
    )
    return keyboard


# ==========================================
# МИДЛВАРЫ
# ==========================================
@dp.message.outer_middleware()
async def access_control_middleware(handler, event: Message, data: dict):
    """Блокировщик доступа: отсекает сообщения от пользователей, которых нет в белом списке ALLOWED_USERS"""
    if ALLOWED_USERS and event.from_user.id not in ALLOWED_USERS:
        logging.warning(f"Action: access_denied | UserID: {event.from_user.id} | Reason: not_in_whitelist")
        return
    return await handler(event, data)


# ==========================================
# ОБРАБОТЧИКИ КНОПОК И КОМАНД
# ==========================================
@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext):
    """Сброс состояния FSM, установка режима FLASH по умолчанию и приветственное сообщение"""
    await state.clear()
    await state.update_data(mode="FLASH")
    logging.info(f"Action: command_start | UserID: {message.from_user.id}")
    
    text = (
        "Здравствуйте! 👋 Я бот, работающий на базе моделей Google Gemini.\n\n"
        "Выберите необходимое действие на клавиатуре ниже, чтобы создать или отредактировать изображение."
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_GENERATE_IMAGE)
async def handle_generate_image_command(message: Message, state: FSMContext):
    """Начало процесса генерации картинки"""
    await state.set_state(BotStates.WAITING_FOR_IMAGE_PROMPT)
    logging.info(f"Action: command_generate_image | UserID: {message.from_user.id}")
    text = (
        "✨ <b>Режим генерации активирован</b>\n\n"
        "Пожалуйста, отправьте подробное описание (промпт) для изображения, которое вы ходите создать. Поддерживается текстовый и голосовой ввод."
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_EDIT_IMAGE)
async def handle_edit_image_command(message: Message, state: FSMContext):
    """Начало процесса редактирования фото"""
    await state.set_state(BotStates.WAITING_FOR_PHOTO_TO_EDIT)
    logging.info(f"Action: command_edit_image | UserID: {message.from_user.id}")
    kb = await get_main_keyboard(state)
    await message.answer("📸 Отправьте исходную <b>фотографию</b> в чат. После этого мы укажем, что именно нужно на ней изменить.", reply_markup=kb)

@dp.message(F.text == BTN_HELP)
async def command_help(message: Message, state: FSMContext):
    """Отображение краткой справочной информации по боту"""
    await state.set_state(None)
    logging.info(f"Action: command_help | UserID: {message.from_user.id}")
    text = (
        "💡 <b>Краткое руководство:</b>\n\n"
        "• <b>Сгенерировать фото</b>: Создание изображения с нуля на основе вашего описания.\n"
        "• <b>Изменить фото</b>: Редактирование существующей фотографии по дополнительной инструкции.\n"
        "• <b>Режимы качества (PRO/FLASH)</b>: FLASH подходит для быстрых скетчей и ответов, а PRO нужен для высококачественной проработки деталей."
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_MODE_PRO)
async def command_mode_pro(message: Message, state: FSMContext):
    """Переключение на режим PRO: тяжелые модели Gemini"""
    await state.update_data(mode="PRO")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: PRO")
    kb = await get_main_keyboard(state)
    await message.answer("💎 Включен режим PRO (Высокая детализация и качество).", reply_markup=kb)

@dp.message(F.text == BTN_MODE_FLASH)
async def command_mode_flash(message: Message, state: FSMContext):
    """Переключение на легковесный и быстрый режим FLASH"""
    await state.update_data(mode="FLASH")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: FLASH")
    kb = await get_main_keyboard(state)
    await message.answer("⚡️ Включен режим FLASH (Оптимизация и высокая скорость).", reply_markup=kb)


# ==========================================
# ЛОГИКА АПИ-ЗАПРОСОВ К GEMINI
# ==========================================
async def handle_genai_error(e: APIError, status_msg: Message):
    """Обработка частых API ошибок Gemini и вывд пользователю"""
    if e.code == 400:
        await status_msg.edit_text("⚠️ Ошибка: Запрос отклонён фильтрами безопасности. Попробуйте изменить формулировку.")
    elif e.code == 429:
        await status_msg.edit_text("⏳ Превышен лимит запросов. Мы отправили слишком много команд. Пожалуйста, подождите минуту.")
    elif e.code >= 500:
        await status_msg.edit_text("🔌 Серверы Google Gemini временно недоступны. Пожалуйста, попробуйте запрос позже.")
    else:
        await status_msg.edit_text(f"⚙️ Произошла неизвестная ошибка при обращении к API: {e.message}. Попробуйте ещё раз.")

async def generate_image_from_text(prompt: str, mode: str, status_msg: Message) -> bytes | None:
    """Герерация изображения с нуля по тексту"""
    model_name = IMAGE_GEN_MODELS.get(mode, IMAGE_GEN_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: generate_image | Model: {model_name}")
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
        return None
    except APIError as e:
        logging.error(f"Action: api_error | Type: generate_image | Model: {model_name} | Error: {e.message}")
        await handle_genai_error(e, status_msg)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: generate_image | Model: {model_name} | Error: {e}")
        await status_msg.edit_text("😔 Произошел внутренний сбой сервиса генерации. Попробуйте снова чуть позже.")
        return None

async def edit_image_with_prompt(image_bytes: bytes, prompt: str, mode: str, status_msg: Message) -> bytes | None:
    """Изменение уже существующего изображения в соответствии с промптом"""
    model_name = IMAGE_EDIT_MODELS.get(mode, IMAGE_EDIT_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: edit_image | Model: {model_name}")
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
        return None
    except APIError as e:
        logging.error(f"Action: api_error | Type: edit_image | Model: {model_name} | Error: {e.message}")
        await handle_genai_error(e, status_msg)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: edit_image | Model: {model_name} | Error: {e}")
        await status_msg.edit_text("😔 Сервис редактирования недоступен. Попробуйте повторить операцию позднее.")
        return None

async def transcribe_audio(audio_bytes: bytes, mode: str, status_msg: Message) -> str | None:
    """Конвертация голосового сообщения в текст с помощью Gemini text/audio моделей"""
    model_name = TEXT_AUDIO_MODELS.get(mode, TEXT_AUDIO_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: transcribe_audio | Model: {model_name}")
    contents = [
        genai_types.Part.from_bytes(data=audio_bytes, mime_type='audio/ogg'),
        "Транскрибируй это голосовое сообщение в текст. Выведи только распознанный текст без лишних слов."
    ]
    try:
        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=contents
        )
        if response.text:
            return response.text.strip()
        return None
    except APIError as e:
        logging.error(f"Action: api_error | Type: transcribe_audio | Model: {model_name} | Error: {e.message}")
        await handle_genai_error(e, status_msg)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: transcribe_audio | Model: {model_name} | Error: {e}")
        await status_msg.edit_text("⚠️ Не удалось распознать голосовое сообщение. Пожалуйста, попробуйте написать текстом.")
        return None


# ==========================================
# ОБРАБОТКА ДАННЫХ ВВОДА (ТЕКСТ/ГОЛОС/ФОТО)
# ==========================================
async def process_text_or_voice_prompt(text: str, message: Message, bot: Bot, state: FSMContext, status_msg: Message | None = None):
    """
    Единая логика для обработки финального текста:
    Принимает готовый текст (неважно, написан он клавиатурой или расшифрован из голоса) и направляет в нужную API-функцию.
    """
    current_state = await state.get_state()
    data = await state.get_data()
    mode = data.get("mode", "FLASH")
    
    # Сценарий генерации фото с нуля
    if current_state == BotStates.WAITING_FOR_IMAGE_PROMPT.state:
        logging.info(f"Action: start_art_generation | UserID: {message.from_user.id} | Prompt: {text}")
        if not status_msg:
            status_msg = await message.answer("🎨 Процесс генерации запущен, ожидайте...")
        else:
            await status_msg.edit_text("🎨 Процесс генерации запущен, ожидайте...")
            
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        image_bytes = await generate_image_from_text(text, mode, status_msg)
        
        if image_bytes:
            await message.answer_photo(types.BufferedInputFile(image_bytes, filename="art.jpg"))
            await state.set_state(None)
            await status_msg.delete()
            logging.info(f"Action: success_art | UserID: {message.from_user.id}")
        
    # Сценарий редактирования существующего фото
    elif current_state == BotStates.WAITING_FOR_EDIT_PROMPT.state:
        edit_file_id = data.get("edit_photo_file_id")
        if not edit_file_id:
            msg = "⚠️ Возникла проблема с загрузкой вашей фотографии. Выберите «🪄 Изменить фото» в меню и попробуйте отправить еще раз."
            if status_msg:
                await status_msg.edit_text(msg)
            else:
                await message.answer(msg)
            await state.set_state(None)
            return

        logging.info(f"Action: start_edit_generation | UserID: {message.from_user.id} | Prompt: {text}")

        if not status_msg:
            status_msg = await message.answer("📥 Готовим вашу фотографию к преобразованиям...")
        else:
            await status_msg.edit_text("📥 Готовим вашу фотографию к преобразованиям...")
            
        # Загружаем фото прямо перед отправкой в ИИ для экономии памяти
        try:
            file = await bot.get_file(edit_file_id)
            downloaded_file = await bot.download_file(file.file_path)
            image_bytes = downloaded_file.read()
            
            await status_msg.edit_text("🪄 Генерируем изменения... Пожалуйста, подождите результат.")
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            
            edited_image_bytes = await edit_image_with_prompt(image_bytes, text, mode, status_msg)
            
            if edited_image_bytes:
                await message.answer_photo(types.BufferedInputFile(edited_image_bytes, filename="edited.jpg"))
                await state.set_state(None)
                await state.update_data(edit_photo_file_id=None)
                await status_msg.delete()
                logging.info(f"Action: success_edit | UserID: {message.from_user.id}")
        except Exception as e:
            logging.error(f"Action: error_download_edit | UserID: {message.from_user.id} | Error: {e}")
            await status_msg.edit_text("😢 Ошибка при загрузке и обработке фотографии из Telegram. Пожалуйста, попробуйте повторить операцию.")

    # Если пользователь написал текст, но ожидалось фото
    elif current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        msg = "Пожалуйста, отправьте именно <b>фотографию</b> (изображение)."
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)

    # Состояние не задано — просим выбрать действие в меню
    else:
        msg = "Пожалуйста, сначала выберите нужное действие в меню бота (создать или изменить)."
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot, state: FSMContext):
    """Делегирование обработки обычного текста напрямую в универсальную функцию"""
    await process_text_or_voice_prompt(message.text, message, bot, state)

@dp.message(F.voice)
async def handle_user_voice(message: Message, bot: Bot, state: FSMContext):
    """Обработка голосовых сообщений: скачивание, транскрибация и делегирование в универсальную функцию"""
    current_state = await state.get_state()

    # Если бот ждал фото
    if current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        await message.answer("В рамках текущего действия ожидается <b>фотография</b>, а не голосовое сообщение.")
        return
        
    # Если бот ожидает команды
    if not current_state:
        await message.answer("Пожалуйста, сначала выберите нужное действие в меню бота (создать или изменить).")
        return

    logging.info(f"Action: receive_voice | UserID: {message.from_user.id}")
    status_msg = await message.answer("🎧 Принимаю ваше аудиосообщение...")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        file_id = message.voice.file_id
        file = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file.file_path)
        audio_bytes = downloaded_file.read()

        data = await state.get_data()
        mode = data.get("mode", "FLASH")
        
        await status_msg.edit_text("✍️ Перевожу голос в текст...")
        text = await transcribe_audio(audio_bytes, mode, status_msg)

        if text:
            # Экранирование спецсимволов и вывод транскрибации для проверки
            safe_text = format_html_response(text)
            await message.answer(f"🎙 <i>Распознанный текст:</i> {safe_text}")
            await process_text_or_voice_prompt(text, message, bot, state, status_msg)
            
    except Exception as e:
        logging.error(f"Action: error_voice_handling | UserID: {message.from_user.id} | Error: {e}")
        await status_msg.edit_text("⚠️ Ошибка: не удалось загрузить аудиосообщение. Попробуйте записать и отправить повторно.")

@dp.message(F.photo)
async def handle_user_photo(message: Message, bot: Bot, state: FSMContext):
    """Обработка загруженных пользователем фотографий"""
    current_state = await state.get_state()
    
    # Если бот конкретно ждал фото для редактирования
    if current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        file_id = message.photo[-1].file_id
        
        # Сохраняем только file_id в хранилище состояний Redis (или In-Memory) для экономии места
        await state.update_data(edit_photo_file_id=file_id)
        await state.set_state(BotStates.WAITING_FOR_EDIT_PROMPT)
        logging.info(f"Action: receive_photo_for_edit | UserID: {message.from_user.id}")
        
        await message.answer("📸 Фотография загружена. Напишите или отправьте голосом инструкцию: что нужно изменить на снимке?")
        
    # Если пользователь отправляет фото, когда его уже просили написать инструкцию
    elif current_state == BotStates.WAITING_FOR_EDIT_PROMPT.state:
         await message.answer("Фотография уже получена! Теперь просто отправьте текстовое или голосовое описание того, что нужно изменить.")

    # Если пользователь отправил фото в меню генерации с нуля (где нужен промпт)
    elif current_state == BotStates.WAITING_FOR_IMAGE_PROMPT.state:
        await message.answer("Данный режим работы поддерживает только текстовые запросы для создания изображений. Если вы хотите изменить фото, выберите «🪄 Изменить фото» в меню.")
        
    # Если действие в меню не было выбрано
    else:
        await message.answer("Сначала выберите кнопку «🪄 Изменить фото» в меню бота, затем отправляйте изображение.")

@dp.message()
async def handle_other_media(message: Message):
    """Заглушка для неподдерживаемого контента: видео, файлов, стикеров и т.д."""
    await message.answer("Извините, на данный момент я поддерживаю только текстовые описания, голосовые сообщения и обычные фотографии. Формат файлов, видео или стикеров не поддерживается.")


# ==========================================
# ТОЧКА ВХОДА И ЗАПУСК ПРИЛОЖЕНИЯ
# ==========================================
async def main():
    """Главная функция для конфигурации и запуска aiogram бота в режимах webhook/polling"""
    if WEBHOOK_URL:
        logging.info(f"Запуск бота через Webhook на порту {PORT}")
        app = web.Application()
        # Для secret_token телеграм допускает только символы A-Z, a-z, 0-9, _ и -
        webhook_secret = TELEGRAM_BOT_TOKEN.replace(":", "")
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=webhook_secret
        )
        webhook_requests_handler.register(app, path="/webhook")
        setup_application(app, dp, bot=bot)
        
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook", secret_token=webhook_secret)
        
        try:
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
            await site.start()
            
            # Поддерживаем процесс запущенным
            while True:
                await asyncio.sleep(3600)
        finally:
            if redis_client:
                await redis_client.aclose()
    else:
        logging.info("Инициализация локального Polling...")
        await bot.delete_webhook(drop_pending_updates=True) # Сброс старых webhook-настроек, если они есть
        try:
            await dp.start_polling(bot)
        finally:
            if redis_client:
                await redis_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Работа бота завершена администратором.")
