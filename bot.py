import asyncio
import logging
import os
import sys
import textwrap
from io import BytesIO
from PIL import Image

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from google import genai
from google.genai import types
from google.genai.errors import APIError
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG & MODELS ---
ACTIVE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_DEV") or os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

if not all([ACTIVE_TOKEN, GOOGLE_API_KEY, WEBHOOK_URL]):
    sys.exit("CRITICAL: Missing environment variables.")

ALLOWED_USERS = {int(uid.strip()) for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()}

# Исправленные идентификаторы моделей на 2026 год
CASCADES = {
    'pro': {
        'text': ['gemini-2.0-pro-001', 'gemini-2.0-flash'],
        'image': ['imagen-3.0-generate-001', 'imagen-3.0-fast-001']
    },
    'flash': {
        'text': ['gemini-2.0-flash', 'gemini-1.5-flash-8b'],
        'image': ['imagen-3.0-fast-001', 'imagen-2.0-exp']
    }
}

DEFAULT_SAFETY = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
]

USER_MODES: dict[int, str] = {}

# --- BOT SETUP ---
client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=ACTIVE_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ALLOWED_USERS:
            return
        return await handler(event, data)

dp.message.middleware(AuthMiddleware())
dp.include_router(router)

class BotStates(StatesGroup):
    waiting_for_input = State()
    waiting_for_gen_prompt = State()
    waiting_for_vision_img = State()
    waiting_for_vision_q = State()
    waiting_for_edit_img = State()
    waiting_for_edit_prompt = State()

# --- KEYBOARDS ---
BTN_TEXT_VOICE = "💬 Текст / Войс"
BTN_GEN_IMG = "🎨 Генерация"
BTN_VISION = "👁️ Vision"
BTN_EDIT_IMG = "🖌️ Редактор"
BTN_MODE_PRO = "💎 Режим: PRO"
BTN_MODE_FLASH = "⚡ Режим: FLASH"
BTN_CANCEL = "❌ Отмена"

def get_main_kb(user_id: int):
    mode = USER_MODES.get(user_id, 'flash')
    mode_btn = BTN_MODE_FLASH if mode == 'pro' else BTN_MODE_PRO
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TEXT_VOICE), KeyboardButton(text=BTN_GEN_IMG)],
            [KeyboardButton(text=BTN_VISION), KeyboardButton(text=BTN_EDIT_IMG)],
            [KeyboardButton(text=mode_btn), KeyboardButton(text=BTN_CANCEL)]
        ], resize_keyboard=True
    )

cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

# --- CORE LOGIC ---
async def generate_with_fallback(models_list: list[str], is_image: bool = False, **kwargs):
    last_err = None
    for model in models_list:
        try:
            kwargs['model'] = model
            if is_image:
                # Используем метод generate_image для картинок
                return await client.aio.models.generate_image(
                    model=model,
                    prompt=kwargs.get('contents') or kwargs.get('prompt'),
                    config=types.GenerateImageConfig(safety_settings=DEFAULT_SAFETY)
                )
            
            if 'config' not in kwargs:
                kwargs['config'] = types.GenerateContentConfig(safety_settings=DEFAULT_SAFETY)
            return await client.aio.models.generate_content(**kwargs)
            
        except APIError as e:
            last_err = e
            logging.error(f"Model {model} failed: {e}")
            if any(code in str(e) for code in ["429", "503", "500", "404"]): continue
            break
    raise last_err or Exception("Cascade exhausted.")

async def handle_response(message: Message, response, is_image: bool = False):
    if is_image:
        # У Imagen ответ содержит список объектов изображений
        if hasattr(response, 'generated_images') and response.generated_images:
            img_data = response.generated_images[0].image.data
            await message.reply_photo(photo=BufferedInputFile(img_data, filename="result.jpg"))
            return True
        elif hasattr(response, 'candidates'): # Для мультимодального Gemini
            parts = response.candidates[0].content.parts
            for part in parts:
                if part.inline_data:
                    await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="result.jpg"))
                    return True
        await message.answer("Ошибка: Изображение не найдено.")
    else:
        if not response.candidates or not response.candidates[0].content.parts:
            reason = response.candidates[0].finish_reason if response.candidates else "SAFETY_TRIGGER"
            await message.answer(f"⚠️ Блокировка. Причина: `{reason}`")
            return False
        
        text = response.candidates[0].content.parts[0].text or "Пустой ответ."
        for chunk in textwrap.wrap(text, width=4000, replace_whitespace=False):
            await message.answer(chunk)
    return True

# --- HANDLERS (ОСТАЛЬНЫЕ БЕЗ ИЗМЕНЕНИЙ, КРОМЕ ВЫЗОВА FALLBACK) ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    USER_MODES.setdefault(message.from_user.id, 'flash')
    await message.answer("Система готова. Выбери задачу.", reply_markup=get_main_kb(message.from_user.id))

@router.message(F.text.in_([BTN_MODE_PRO, BTN_MODE_FLASH]))
async def toggle_mode(message: Message):
    user_id = message.from_user.id
    new_mode = 'pro' if USER_MODES.get(user_id, 'flash') == 'flash' else 'flash'
    USER_MODES[user_id] = new_mode
    await message.answer(f"Переключено на **{new_mode.upper()}**.", reply_markup=get_main_kb(user_id))

@router.message(F.text == BTN_CANCEL)
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_main_kb(message.from_user.id))

@router.message(F.text == BTN_TEXT_VOICE)
async def btn_text_voice(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_input)
    await message.answer("Жду текст или голос:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_input, F.text | F.voice)
async def handle_text_or_voice(message: Message, state: FSMContext):
    status = await message.answer("⚡ Обработка...")
    try:
        contents = message.text
        if message.voice:
            v_file = await bot.download_file((await bot.get_file(message.voice.file_id)).file_path)
            contents = [types.Part.from_bytes(data=v_file.read(), mime_type='audio/ogg')]
        
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['text'], is_image=False, contents=contents)
        if await handle_response(message, resp):
            await state.clear()
            await message.answer("Готово.", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"Ошибка API: `{e}`")
    finally:
        await status.delete()

@router.message(F.text == BTN_GEN_IMG)
async def btn_gen(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_gen_prompt)
    await message.answer("Введи промпт для генерации:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen(message: Message, state: FSMContext):
    status = await message.answer("🎨 Рендеринг...")
    try:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['image'], is_image=True, contents=message.text)
        if await handle_response(message, resp, is_image=True):
            await state.clear()
            await message.answer("Готово.", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"Ошибка генерации: `{e}`")
    finally:
        await status.delete()

# --- WEBHOOK & MAIN ---
async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()