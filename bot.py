import asyncio, logging, os, sys, textwrap
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

# --- CONFIG ---
ACTIVE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

if not all([ACTIVE_TOKEN, GOOGLE_API_KEY, WEBHOOK_URL]):
    sys.exit("CRITICAL: Missing environment variables.")

ALLOWED_USERS = {int(uid.strip()) for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()}

# 2026 Model Alignment
CASCADES = {
    'pro': {
        'text': ['gemini-3.0-pro', 'gemini-3.0-flash'],
        'image': ['nano-banana-pro', 'nano-banana']
    },
    'flash': {
        'text': ['gemini-3.0-flash', 'gemini-2.5-flash'],
        'image': ['nano-banana', 'imagen-3.0-fast-001']
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
            logging.warning(f"Unauthorized access attempt: {user.id if user else 'Unknown'}")
            return
        return await handler(event, data)

dp.message.middleware(AuthMiddleware())
dp.include_router(router)

class BotStates(StatesGroup):
    waiting_for_input = State()
    waiting_for_gen_prompt = State()

# --- LOGIC ---
async def generate_with_fallback(models_list: list[str], is_image: bool = False, payload: str = None):
    last_err = None
    for model in models_list:
        try:
            if is_image:
                return await client.aio.models.generate_image(
                    model=model,
                    prompt=payload,
                    config=types.GenerateImageConfig(safety_settings=DEFAULT_SAFETY)
                )
            return await client.aio.models.generate_content(
                model=model,
                contents=payload,
                config=types.GenerateContentConfig(safety_settings=DEFAULT_SAFETY)
            )
        except APIError as e:
            last_err = e
            logging.error(f"Model {model} failed: {e}")
            if any(code in str(e) for code in ["429", "503", "500"]): continue
            break
    raise last_err or Exception("Cascade exhausted.")

async def handle_response(message: Message, response, is_image: bool = False):
    if is_image:
        if response.generated_images:
            img_data = response.generated_images[0].image.data
            await message.reply_photo(photo=BufferedInputFile(img_data, filename="res.jpg"))
            return True
    else:
        if not response.candidates:
            await message.answer("⚠️ Safety Blocked.")
            return False
        text = response.candidates[0].content.parts[0].text
        for chunk in textwrap.wrap(text, width=4000):
            await message.answer(chunk)
    return True

# --- HANDLERS ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    USER_MODES.setdefault(message.from_user.id, 'flash')
    await message.answer("Ready.", reply_markup=get_main_kb(message.from_user.id))

@router.message(F.text == "🎨 Генерация")
async def btn_gen(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_gen_prompt)
    await message.answer("Prompt:", reply_markup=cancel_kb)

@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen(message: Message, state: FSMContext):
    status = await message.answer("🎨 Rendering...")
    try:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['image'], is_image=True, payload=message.text)
        if await handle_response(message, resp, is_image=True):
            await state.clear()
    except Exception as e:
        await message.answer(f"Error: `{e}`")
    finally:
        await status.delete()

# --- WEBHOOK & HEALTH CHECK ---
async def handle_index(request):
    return web.Response(text="Bot is alive", status=200)

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    app.router.add_get("/", handle_index) # Fix for Render 404
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()