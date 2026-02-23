import asyncio
import logging
import os
import sys
import textwrap
from io import BytesIO
from PIL import Image

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message, ReplyKeyboardMarkup, KeyboardButton
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ALLOWED_USERS_STR = os.getenv("ALLOWED_USERS", "")
PORT = int(os.getenv("PORT", 8080))

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    sys.exit("CRITICAL: TELEGRAM_BOT_TOKEN или GOOGLE_API_KEY отсутствует.")

ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_STR.split(",") if uid.strip().isdigit()}

MODEL_TEXT_VISION = 'gemini-2.5-flash'
MODEL_IMAGE_GEN = 'gemini-2.5-flash-image'

BTN_TEXT_VOICE = "💬 Написать / Сказать"
BTN_GEN_IMG = "🎨 Нарисовать"
BTN_VISION = "👁️ Описать фото"
BTN_EDIT_IMG = "🖌️ Изменить фото"
BTN_CANCEL = "❌ Отмена"

MENU_COMMANDS = [BTN_TEXT_VOICE, BTN_GEN_IMG, BTN_VISION, BTN_EDIT_IMG, BTN_CANCEL]

client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

logging.basicConfig(level=logging.INFO)

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

@router.message(~F.from_user.id.in_(ALLOWED_USERS))
async def unauthorized(message: Message):
    await message.answer("Доступ закрыт.")

@router.message(F.text == BTN_CANCEL)
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=menu_kb)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет. Выбирай действие.", reply_markup=menu_kb)

@router.message(F.text.in_(MENU_COMMANDS), ~StateFilter(None))
async def handle_menu_in_state(message: Message, state: FSMContext):
    await state.clear()
    return await dp.feed_update(bot, message.model_copy(update={"text": message.text}))

@router.message(F.text == BTN_TEXT_VOICE, StateFilter(None))
async def btn_text_voice(message: Message, state: FSMContext):
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
        await send_long_message(message, response.text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    finally:
        await status_msg.delete()
        await state.clear()

@router.message(F.text == BTN_GEN_IMG, StateFilter(None))
async def btn_gen(message: Message, state: FSMContext):
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
        for part in response.parts:
            if part.inline_data:
                await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="gen.jpg"))
                break
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    finally:
        await status_msg.delete()
        await state.clear()

@router.message(F.text == BTN_VISION, StateFilter(None))
async def btn_vision(message: Message, state: FSMContext):
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

@router.message(F.text == BTN_EDIT_IMG, StateFilter(None))
async def btn_edit(message: Message, state: FSMContext):
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
        await send_long_message(message, response.text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    finally:
        await status_msg.delete()
        await state.clear()

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
        for part in response.parts:
            if part.inline_data:
                await message.reply_photo(photo=BufferedInputFile(part.inline_data.data, filename="edited.jpg"))
                break
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    finally:
        await status_msg.delete()
        await state.clear()

@router.message(F.text)
async def fallback(message: Message):
    await message.answer("Не понял. Жми кнопки.", reply_markup=menu_kb)

async def main():
    app = web.Application()
    app.router.add_get('/', lambda request: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())