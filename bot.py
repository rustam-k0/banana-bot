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
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from google import genai
from google.genai import types
from google.genai.errors import APIError
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN_PROD = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_TOKEN_DEV = os.getenv("TELEGRAM_BOT_TOKEN_DEV")
ACTIVE_TOKEN = TELEGRAM_BOT_TOKEN_DEV if TELEGRAM_BOT_TOKEN_DEV else TELEGRAM_BOT_TOKEN_PROD

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

if not ACTIVE_TOKEN or not GOOGLE_API_KEY or not WEBHOOK_URL:
    sys.exit("CRITICAL: Отсутствуют ключи или WEBHOOK_URL в .env.")

try:
    ALLOWED_USERS_STR = os.getenv("ALLOWED_USERS", "")
    ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_STR.split(",") if uid.strip()}
except ValueError:
    sys.exit("CRITICAL: ALLOWED_USERS содержит невалидные ID.")

MODEL_TEXT_VISION = 'gemini-2.5-flash'
MODEL_IMAGE_GEN = 'gemini-2.5-flash-image'

BTN_TEXT_VOICE = "💬 Написать / Сказать"
BTN_GEN_IMG = "🎨 Нарисовать"
BTN_VISION = "👁️ Описать фото"
BTN_EDIT_IMG = "🖌️ Изменить фото"
BTN_CANCEL = "❌ Отмена"

client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=ACTIVE_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()

logging.basicConfig(level=logging.INFO)

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ALLOWED_USERS:
            username = user.username if user else "unknown"
            user_id = user.id if user else "unknown"
            logging.warning(f"UNAUTHORIZED ACCESS: ID {user_id} | @{username}")
            if event.message:
                await event.message.answer(f"Извини, доступ закрыт.\nТвой ID: `{user_id}`")
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

menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TEXT_VOICE), KeyboardButton(text=BTN_GEN_IMG)],
        [KeyboardButton(text=BTN_VISION), KeyboardButton(text=BTN_EDIT_IMG)],
        [KeyboardButton(text=BTN_CANCEL)]
    ],
    resize_keyboard=True
)
cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

async def send_long_message(message: Message, text: str):
    if not text: return
    for chunk in textwrap.wrap(text, width=4000, replace_whitespace=False):
        await message.answer(chunk)

async def handle_api_error(message: Message, state: FSMContext, exception: Exception):
    if isinstance(exception, APIError):
        logging.error(f"Gemini API Error: {exception}")
        await message.answer("Ошибка API Gemini. Запрос отклонен политикой безопасности или недоступен.")
    else:
        logging.error(f"Unexpected Error: {exception}")
        await message.answer("Внутренняя ошибка при обработке запроса.")
    await state.clear()

@router.message(F.text == BTN_CANCEL)
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=menu_kb)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет. Выбирай действие.", reply_markup=menu_kb)

@router.message(F.text == BTN_TEXT_VOICE)
async def btn_text_voice(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Жду текст или войс:", reply_markup=cancel_kb)
    await state.set_state(BotStates.waiting_for_input)

@router.message(BotStates.waiting_for_input, F.text | F.voice)
async def handle_text_or_voice(message: Message, state: FSMContext):
    status_msg = await message.answer("Думаю...")
    try:
        if message.voice:
            file_info = await bot.get_file(message.voice.file_id)
            voice_file = await bot.download_file(file_info.file_path)
            contents = [types.Part.from_bytes(data=voice_file.read(), mime_type='audio/ogg')]
        else:
            contents = message.text

        response = await client.aio.models.generate_content(model=MODEL_TEXT_VISION, contents=contents)
        await send_long_message(message, response.text or "Пустой ответ от модели.")
        await state.clear()
    except Exception as e:
        await handle_api_error(message, state, e)
    finally:
        await status_msg.delete()

@router.message(F.text == BTN_GEN_IMG)
async def btn_gen(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Промпт для генерации:", reply_markup=cancel_kb)
    await state.set_state(BotStates.waiting_for_gen_prompt)

@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen(message: Message, state: FSMContext):
    status_msg = await message.answer("Рисую...")
    try:
        response = await client.aio.models.generate_content(
            model=MODEL_IMAGE_GEN,
            contents=message.text,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="1:1"))
        )
        if not response.parts:
             await message.answer("Запрос заблокирован фильтрами безопасности Gemini.")
             return
             
        for part in response.parts:
            if part.inline_data:
                await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="gen.jpg"))
                break
        await state.clear()
    except Exception as e:
        await handle_api_error(message, state, e)
    finally:
        await status_msg.delete()

@router.message(F.text == BTN_VISION)
async def btn_vision(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Кидай фото:", reply_markup=cancel_kb)
    await state.set_state(BotStates.waiting_for_vision_img)

@router.message(BotStates.waiting_for_vision_img, F.photo)
async def handle_vision_img(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    if message.caption:
        await process_image(message, state, message.caption, photo_id, MODEL_TEXT_VISION)
    else:
        await state.update_data(photo_id=photo_id)
        await message.answer("Что рассказать про фото?", reply_markup=cancel_kb)
        await state.set_state(BotStates.waiting_for_vision_q)

@router.message(BotStates.waiting_for_vision_q, F.text | F.voice)
async def handle_vision_q(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt = message.text or "Опиши это." 
    await process_image(message, state, prompt, data['photo_id'], MODEL_TEXT_VISION)

@router.message(F.text == BTN_EDIT_IMG)
async def btn_edit(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Кидай фото для изменения:", reply_markup=cancel_kb)
    await state.set_state(BotStates.waiting_for_edit_img)

@router.message(BotStates.waiting_for_edit_img, F.photo)
async def handle_edit_img(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    if message.caption:
        await process_edit(message, state, message.caption, photo_id)
    else:
        await state.update_data(photo_id=photo_id)
        await message.answer("Что изменить?", reply_markup=cancel_kb)
        await state.set_state(BotStates.waiting_for_edit_prompt)

@router.message(BotStates.waiting_for_edit_prompt, F.text)
async def handle_edit_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    await process_edit(message, state, message.text, data['photo_id'])

async def process_image(message: Message, state: FSMContext, prompt: str, photo_id: str, model: str):
    status_msg = await message.answer("Смотрю...")
    try:
        file_info = await bot.get_file(photo_id)
        img_file = await bot.download_file(file_info.file_path)
        img = await asyncio.to_thread(Image.open, BytesIO(img_file.read()))
        
        response = await client.aio.models.generate_content(model=model, contents=[img, prompt])
        await send_long_message(message, response.text or "Пустой ответ.")
        await state.clear()
    except Exception as e:
        await handle_api_error(message, state, e)
    finally:
        await status_msg.delete()

async def process_edit(message: Message, state: FSMContext, prompt: str, photo_id: str):
    status_msg = await message.answer("Изменяю...")
    try:
        file_info = await bot.get_file(photo_id)
        img_file = await bot.download_file(file_info.file_path)
        img = await asyncio.to_thread(Image.open, BytesIO(img_file.read()))
        
        response = await client.aio.models.generate_content(
            model=MODEL_IMAGE_GEN,
            contents=[img, prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"])
        )
        if not response.parts:
             await message.answer("Изменение отклонено фильтрами Gemini.")
             return

        for part in response.parts:
            if part.inline_data:
                await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="edited.jpg"))
                break
        await state.clear()
    except Exception as e:
        await handle_api_error(message, state, e)
    finally:
        await status_msg.delete()

@router.message(F.text)
async def fallback(message: Message):
    await message.answer("Не понял. Жми кнопки.", reply_markup=menu_kb)

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()