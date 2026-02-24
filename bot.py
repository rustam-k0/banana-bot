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

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOWED_USERS_ENV = os.getenv("ALLOWED_USERS", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))
REDIS_URL = os.getenv("REDIS_URL")

# Парсим ID разрешенных пользователей
ALLOWED_USERS = set()
for u in ALLOWED_USERS_ENV.split(","):
    if u.strip().isdigit():
        ALLOWED_USERS.add(int(u.strip()))

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    logging.error("Не найден TELEGRAM_BOT_TOKEN или GOOGLE_API_KEY в .env")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("google.api_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
gemini_client = genai.Client(api_key=GOOGLE_API_KEY, http_options={"api_version": "v1alpha"})

# Настройка Redis и FSM Storage
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        storage = RedisStorage(redis=redis_client)
        logging.info("Redis подключен для хранения состояний FSM.")
    except Exception as e:
        logging.error(f"Ошибка при подключении к Redis: {e}")
        redis_client = None
        storage = MemoryStorage()
        logging.info("Используется in-memory хранилище FSM (ВНИМАНИЕ: данные сбросятся при рестарте).")
else:
    redis_client = None
    storage = MemoryStorage()
    logging.info("REDIS_URL не найден, используется in-memory хранилище FSM (ВНИМАНИЕ: данные сбросятся при рестарте).")

dp = Dispatcher(storage=storage)

# ==========================================
# КОНСТАНТЫ И СОСТОЯНИЯ
# ==========================================
BTN_ART = "🎨 Создать шедевр"
BTN_EDIT = "🪄 Преобразить фото"
BTN_HELP = "💡 Подсказка"
BTN_MODE_PRO = "💎 Детально (PRO)"
BTN_MODE_FLASH = "⚡️ Быстро (FLASH)"

class BotStates(StatesGroup):
    WAITING_ART = State()
    WAITING_EDIT_PHOTO = State()
    WAITING_EDIT_PROMPT = State()

IMAGE_GEN_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-2.5-flash-image"]
}

IMAGE_EDIT_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-2.5-flash-image"]
}

TEXT_AUDIO_MODELS = {
    "PRO": ["gemini-3.1-pro-preview"],
    "FLASH": ["gemini-3-flash-preview"]
}

def format_html_response(text: str) -> str:
    """Экранирует спецсимволы и конвертирует базовый Markdown (жирный, моноширинный) в HTML"""
    text = html.escape(text, quote=False)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text

async def get_main_keyboard(state: FSMContext) -> ReplyKeyboardMarkup:
    data = await state.get_data()
    current_mode = data.get("mode", "FLASH")
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
        logging.warning(f"Action: access_denied | UserID: {event.from_user.id} | Reason: not_in_whitelist")
        return
    return await handler(event, data)

# ==========================================
# ОБРАБОТЧИКИ КНОПОК
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(mode="FLASH")
    logging.info(f"Action: cmd_start | UserID: {message.from_user.id}")
    
    text = (
        "👋 Привет! Я твой дружелюбный ИИ-художник. 🎨\n\n"
        "Давай сотворим что-нибудь невероятное! Выбери нужное действие в меню ниже: 👇"
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_ART)
async def cmd_art(message: Message, state: FSMContext):
    await state.set_state(BotStates.WAITING_ART)
    logging.info(f"Action: cmd_art | UserID: {message.from_user.id}")
    text = (
        "✨ <b>Что будем рисовать?</b>\n\n"
        "Расскажи мне свою идею во всех красках: что должно быть на картинке, в каком стиле и цветовой гамме. Чем больше деталей, тем волшебнее будет результат! 🪄"
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_EDIT)
async def cmd_edit(message: Message, state: FSMContext):
    await state.set_state(BotStates.WAITING_EDIT_PHOTO)
    logging.info(f"Action: cmd_edit | UserID: {message.from_user.id}")
    kb = await get_main_keyboard(state)
    await message.answer("📸 Жду <b>фотографию</b>, над которой мы будем колдовать! Отправь её прямо сюда.", reply_markup=kb)

@dp.message(F.text == BTN_HELP)
async def cmd_help(message: Message, state: FSMContext):
    await state.set_state(None)
    logging.info(f"Action: cmd_help | UserID: {message.from_user.id}")
    text = (
        "💡 <b>Как со мной работать:</b>\n\n"
        "• <b>Создать шедевр</b> — просто опиши свою задумку, и я нарисую её с нуля! 🎨\n"
        "• <b>Преобразить фото</b> — отправь снимок и скажи, что именно добавить или убрать! 🪄\n"
        "• <b>Режимы</b> — выбирай между молниеносной скоростью (FLASH) и невероятной детализацией (PRO)! 🌟"
    )
    kb = await get_main_keyboard(state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == BTN_MODE_PRO)
async def cmd_set_pro(message: Message, state: FSMContext):
    await state.update_data(mode="PRO")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: PRO")
    kb = await get_main_keyboard(state)
    await message.answer("💎 Отлично! Теперь я буду рисовать максимально детализировано и качественно (PRO). 🎨", reply_markup=kb)

@dp.message(F.text == BTN_MODE_FLASH)
async def cmd_set_flash(message: Message, state: FSMContext):
    await state.update_data(mode="FLASH")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: FLASH")
    kb = await get_main_keyboard(state)
    await message.answer("⚡️ Супер! Включаю турборежим (FLASH) — результаты будут появляться почти мгновенно! 🚀", reply_markup=kb)

# ==========================================
# ФУНКЦИИ ГЕНЕРАЦИИ ЧЕРЕЗ GEMINI
# ==========================================
async def handle_genai_error(e: APIError, status_msg: Message):
    if e.code == 400:
        await status_msg.edit_text("🥺 Упс... Твой запрос отклонён фильтрами безопасности. Давай попробуем сформулировать иначе? 🌱")
    elif e.code == 429:
        await status_msg.edit_text("⏳ Ой, мы отправили слишком много запросов! Давай немного отдохнём и попробуем снова через пару минут? ☕️")
    elif e.code >= 500:
        await status_msg.edit_text("🔌 Серверы сейчас немного устали и временно недоступны. Пожалуйста, загляни чуточку позже! 🛠")
    else:
        await status_msg.edit_text(f"⚙️ Ой-ой, произошла непредвиденная ошибка ИИ: {e.message}. Попробуем ещё раз? 🔄")

async def generate_image_cascade(prompt: str, mode: str, status_msg: Message) -> bytes | None:
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
        await status_msg.edit_text("😔 К сожалению, рисование сейчас недоступно. Давай попробуем чуть позже? 🕰")
        return None

async def edit_image(image_bytes: bytes, prompt: str, mode: str, status_msg: Message) -> bytes | None:
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
        await status_msg.edit_text("😔 Сервис редактирования пока отдыхает. Попробуй вернуться к этому чуть позже! 🕰")
        return None

async def transcribe_audio(audio_bytes: bytes, mode: str, status_msg: Message) -> str | None:
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
        await status_msg.edit_text("😔 Я сейчас не могу распознать голос. Давай попробуем позже или напиши текстом! ⌨️")
        return None

# ==========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ
# ==========================================
async def process_user_text_input(text: str, message: Message, bot: Bot, state: FSMContext, status_msg: Message | None = None):
    """Общая логика обработки текста или расшифрованного голоса"""
    current_state = await state.get_state()
    data = await state.get_data()
    mode = data.get("mode", "FLASH")
    
    if current_state == BotStates.WAITING_ART.state:
        logging.info(f"Action: start_art_generation | UserID: {message.from_user.id} | Prompt: {text}")
        if not status_msg:
            status_msg = await message.answer("🎨 Рисую твою задумку... Немного магии! ✨")
        else:
            await status_msg.edit_text("🎨 Рисую твою задумку... Немного магии! ✨")
            
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        image_bytes = await generate_image_cascade(text, mode, status_msg)
        if image_bytes:
            await message.answer_photo(types.BufferedInputFile(image_bytes, filename="art.jpg"))
            await state.set_state(None)
            await status_msg.delete()
            logging.info(f"Action: success_art | UserID: {message.from_user.id}")
        
    elif current_state == BotStates.WAITING_EDIT_PROMPT.state:
        edit_file_id = data.get("edit_photo_file_id")
        if not edit_file_id:
            msg = "🙈 Кажется, фотография потерялась... Нажми «🪄 Преобразить фото» и отправь её ещё раз! 📸"
            if status_msg:
                await status_msg.edit_text(msg)
            else:
                await message.answer(msg)
            await state.set_state(None)
            return

        logging.info(f"Action: start_edit_generation | UserID: {message.from_user.id} | Prompt: {text}")

        if not status_msg:
            status_msg = await message.answer("📥 Получаю твоё фото... Почти готово! ⏳")
        else:
            await status_msg.edit_text("📥 Получаю твоё фото... Почти готово! ⏳")
            
        # Загружаем фото прямо перед отправкой в LLM (экономия памяти и ускорение FSM Storage)
        try:
            file = await bot.get_file(edit_file_id)
            downloaded_file = await bot.download_file(file.file_path)
            image_bytes = downloaded_file.read()
            
            await status_msg.edit_text("🪄 Колдую над деталями... Ещё чуть-чуть! ✨")
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            edited_image_bytes = await edit_image(image_bytes, text, mode, status_msg)
            
            if edited_image_bytes:
                await message.answer_photo(types.BufferedInputFile(edited_image_bytes, filename="edited.jpg"))
                await state.set_state(None)
                await state.update_data(edit_photo_file_id=None)
                await status_msg.delete()
                logging.info(f"Action: success_edit | UserID: {message.from_user.id}")
        except Exception as e:
            logging.error(f"Action: error_download_edit | UserID: {message.from_user.id} | Error: {e}")
            await status_msg.edit_text("😢 Что-то пошло не так при обработке фото... Давай попробуем снова? 🔄")

    elif current_state == BotStates.WAITING_EDIT_PHOTO.state:
        msg = "🤗 Пожалуйста, отправь мне именно <b>фотографию</b>, а не текст!"
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)

    else:
        msg = "👇 Пожалуйста, сначала выбери действие в меню внизу экрана! 👀"
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot, state: FSMContext):
    await process_user_text_input(message.text, message, bot, state)

@dp.message(F.voice)
async def handle_user_voice(message: Message, bot: Bot, state: FSMContext):
    current_state = await state.get_state()

    if current_state == BotStates.WAITING_EDIT_PHOTO.state:
        await message.answer("🤗 Для этого действия мне нужно <b>фото</b>, а не голосовое сообщение!")
        return
        
    if not current_state:
        await message.answer("👇 Давай начнем с выбора действия в меню внизу экрана! 😊")
        return

    logging.info(f"Action: receive_voice | UserID: {message.from_user.id}")
    status_msg = await message.answer("🎧 Внимательно слушаю твоё сообщение...")
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        file_id = message.voice.file_id
        file = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file.file_path)
        audio_bytes = downloaded_file.read()

        data = await state.get_data()
        mode = data.get("mode", "FLASH")
        
        await status_msg.edit_text("✍️ Превращаю голос в текст...")
        text = await transcribe_audio(audio_bytes, mode, status_msg)

        if text:
            # Экранируем и очищаем текст для отображения (в случае спецсимволов и тд)
            safe_text = format_html_response(text)
            await message.answer(f"🎙 <i>Твои слова:</i> {safe_text}")
            await process_user_text_input(text, message, bot, state, status_msg)
            
    except Exception as e:
        logging.error(f"Action: error_voice_handling | UserID: {message.from_user.id} | Error: {e}")
        await status_msg.edit_text("😢 Не удалось загрузить аудио... Попробуй повторить, пожалуйста! 🔄")

@dp.message(F.photo)
async def handle_user_photo(message: Message, bot: Bot, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == BotStates.WAITING_EDIT_PHOTO.state:
        file_id = message.photo[-1].file_id
        
        # Сохраняем ТОЛЬКО file_id вместо скачивания и сохранения целых байтов в FSM
        await state.update_data(edit_photo_file_id=file_id)
        await state.set_state(BotStates.WAITING_EDIT_PROMPT)
        logging.info(f"Action: receive_photo_for_edit | UserID: {message.from_user.id}")
        
        await message.answer("📸 Фото у меня! Что именно хочется изменить? (Напиши текстом или скажи голосом) 🎙")
        
    elif current_state == BotStates.WAITING_EDIT_PROMPT.state:
         await message.answer("🤗 Фото уже у меня! Просто расскажи, что хочется на нём поменять.")

    elif current_state == BotStates.WAITING_ART.state:
        await message.answer("🤗 Режим рисования работает по тексту. Напиши задумку словами, а не кидай фото!")
        
    else:
        await message.answer("👇 Сперва нажми кнопку «🪄 Преобразить фото», а потом скидывай картинку! 📸")

@dp.message()
async def handle_other_media(message: Message):
    await message.answer("🥺 Извини, но я понимаю только обычные фотографии, текст и голосовые сообщения!")


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
        
        try:
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
            await site.start()
            
            while True:
                await asyncio.sleep(3600)
        finally:
            if redis_client:
                await redis_client.aclose()
    else:
        logging.info("Запуск локального Polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await dp.start_polling(bot)
        finally:
            if redis_client:
                await redis_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")
