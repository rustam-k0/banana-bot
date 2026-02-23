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

ACTIVE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_DEV") or os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

if not all([ACTIVE_TOKEN, GOOGLE_API_KEY, WEBHOOK_URL]):
    sys.exit("CRITICAL: Missing environment variables. Check .env.")

try:
    ALLOWED_USERS = {int(uid.strip()) for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()}
except ValueError:
    sys.exit("CRITICAL: Invalid ALLOWED_USERS format.")

# Конфигурация каскадов
CASCADES = {
    'pro': {
        'text': ['gemini-3.1-pro', 'gemini-3.0-flash'],
        'image': ['nano-banana-pro', 'nano-banana']
    },
    'flash': {
        'text': ['gemini-3.0-flash', 'gemini-2.5-flash'],
        'image': ['nano-banana', 'gemini-2.5-flash-image']
    }
}

USER_MODES: dict[int, str] = {}  # Хранит 'pro' или 'flash'. Дефолт - 'flash'

BTN_TEXT_VOICE = "💬 Текст / Войс"
BTN_GEN_IMG = "🎨 Генерация"
BTN_VISION = "👁️ Vision"
BTN_EDIT_IMG = "🖌️ Редактор"
BTN_MODE_PRO = "💎 Режим: PRO"
BTN_MODE_FLASH = "⚡ Режим: FLASH"
BTN_CANCEL = "❌ Отмена"

client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=ACTIVE_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id not in ALLOWED_USERS:
            logging.warning(f"403 Blocked: ID {user.id if user else 'unknown'}")
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

def get_main_kb(user_id: int):
    mode = USER_MODES.get(user_id, 'flash')
    mode_btn = BTN_MODE_FLASH if mode == 'pro' else BTN_MODE_PRO
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TEXT_VOICE), KeyboardButton(text=BTN_GEN_IMG)],
            [KeyboardButton(text=BTN_VISION), KeyboardButton(text=BTN_EDIT_IMG)],
            [KeyboardButton(text=mode_btn), KeyboardButton(text=BTN_CANCEL)]
        ],
        resize_keyboard=True
    )

cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

async def send_long_message(message: Message, text: str):
    if not text: return
    for chunk in textwrap.wrap(text, width=4000, replace_whitespace=False):
        await message.answer(chunk)

async def generate_with_fallback(models_list: list[str], **kwargs):
    last_err = None
    for model in models_list:
        try:
            logging.info(f"Executing: {model}")
            kwargs['model'] = model
            return await client.aio.models.generate_content(**kwargs)
        except APIError as e:
            logging.warning(f"Fallback triggered for {model}: {e.message}")
            last_err = e
            if any(code in str(e) for code in ["429", "503", "500"]):
                continue
            break
    raise last_err or Exception("Cascade exhausted.")

async def handle_error(message: Message, state: FSMContext, e: Exception):
    logging.error(f"Execution failed: {e}")
    await message.answer("Ошибка API или фильтр безопасности.")
    await state.set_state(None)

@router.message(F.text.in_([BTN_MODE_PRO, BTN_MODE_FLASH]))
async def toggle_mode(message: Message, state: FSMContext):
    user_id = message.from_user.id
    current_mode = USER_MODES.get(user_id, 'flash')
    new_mode = 'pro' if current_mode == 'flash' else 'flash'
    USER_MODES[user_id] = new_mode
    
    await state.set_state(None)
    await message.answer(f"Маршрутизация переключена на **{new_mode.upper()}**.", reply_markup=get_main_kb(user_id))

@router.message(F.text == BTN_CANCEL)
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.set_state(None)
    await message.answer("Операция прервана.", reply_markup=get_main_kb(message.from_user.id))

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(None)
    USER_MODES.setdefault(message.from_user.id, 'flash')
    await message.answer("Система готова. Выбери задачу.", reply_markup=get_main_kb(message.from_user.id))

@router.message(F.text == BTN_TEXT_VOICE)
async def btn_text_voice(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_input)
    await message.answer("Ожидаю текст или голосовое сообщение (.ogg):", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_input, F.text | F.voice)
async def handle_text_or_voice(message: Message, state: FSMContext):
    status = await message.answer("Обработка...")
    try:
        if message.voice:
            file_info = await bot.get_file(message.voice.file_id)
            voice_file = await bot.download_file(file_info.file_path)
            contents = [types.Part.from_bytes(data=voice_file.read(), mime_type='audio/ogg')]
        else:
            contents = message.text

        mode = USER_MODES.get(message.from_user.id, 'flash')
        models = CASCADES[mode]['text']
        
        response = await generate_with_fallback(models, contents=contents)
        await send_long_message(message, response.text or "Пустой ответ.")
        await state.set_state(None)
        await message.answer("Готово.", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await handle_error(message, state, e)
    finally:
        await status.delete()

@router.message(F.text == BTN_GEN_IMG)
async def btn_gen(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_gen_prompt)
    await message.answer("Введи промпт для генерации:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen(message: Message, state: FSMContext):
    status = await message.answer("Рендеринг...")
    try:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        models = CASCADES[mode]['image']
        
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1")
        )
        response = await generate_with_fallback(models, contents=message.text, config=config)
        
        if not response.parts:
             await message.answer("Блокировка по safety-фильтрам.")
             return
             
        for part in response.parts:
            if part.inline_data:
                await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="gen.jpg"))
                break
                
        await state.set_state(None)
        await message.answer("Готово.", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await handle_error(message, state, e)
    finally:
        await status.delete()

@router.message(F.text == BTN_VISION)
async def btn_vision(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_vision_img)
    await message.answer("Загрузи изображение:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_vision_img, F.photo)
async def handle_vision_img(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    if message.caption:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        await process_vision_edit(message, state, message.caption, photo_id, CASCADES[mode]['text'])
    else:
        await state.update_data(photo_id=photo_id)
        await state.set_state(BotStates.waiting_for_vision_q)
        await message.answer("Укажи вопрос к фото:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_vision_q, F.text | F.voice)
async def handle_vision_q(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt = message.text or "Опиши это изображение детально."
    mode = USER_MODES.get(message.from_user.id, 'flash')
    await process_vision_edit(message, state, prompt, data['photo_id'], CASCADES[mode]['text'])

@router.message(F.text == BTN_EDIT_IMG)
async def btn_edit(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_edit_img)
    await message.answer("Загрузи исходник для изменения:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_edit_img, F.photo)
async def handle_edit_img(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    if message.caption:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        await process_vision_edit(message, state, message.caption, photo_id, CASCADES[mode]['image'], is_edit=True)
    else:
        await state.update_data(photo_id=photo_id)
        await state.set_state(BotStates.waiting_for_edit_prompt)
        await message.answer("Укажи инструкции для изменения:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_edit_prompt, F.text)
async def handle_edit_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = USER_MODES.get(message.from_user.id, 'flash')
    await process_vision_edit(message, state, message.text, data['photo_id'], CASCADES[mode]['image'], is_edit=True)

async def process_vision_edit(message: Message, state: FSMContext, prompt: str, photo_id: str, models: list, is_edit: bool = False):
    status = await message.answer("Выполнение...")
    try:
        file_info = await bot.get_file(photo_id)
        img_file = await bot.download_file(file_info.file_path)
        img = await asyncio.to_thread(Image.open, BytesIO(img_file.read()))
        
        kwargs = {"contents": [img, prompt]}
        if is_edit:
            kwargs["config"] = types.GenerateContentConfig(response_modalities=["IMAGE"])

        response = await generate_with_fallback(models, **kwargs)
        
        if is_edit:
            if not response.parts:
                 await message.answer("Трансформация отклонена фильтрами.")
                 return
            for part in response.parts:
                if part.inline_data:
                    await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="edited.jpg"))
                    break
        else:
            await send_long_message(message, response.text or "Пустой ответ.")
            
        await state.set_state(None)
        await message.answer("Готово.", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await handle_error(message, state, e)
    finally:
        await status.delete()

@router.message(F.text)
async def fallback(message: Message):
    await message.answer("Используй клавиатуру для навигации.", reply_markup=get_main_kb(message.from_user.id))

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