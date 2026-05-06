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
import redis.asyncio as redis

from config import (
    IMAGE_EDIT_MODELS,
    IMAGE_GEN_MODELS,
    TEXT_AUDIO_MODELS,
    load_config,
)
from texts import TEXTS

# Clean up verbose third-party logger output
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("google.api_core").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Load environment variables
load_dotenv()

# Read main configurations
config = load_config()
TELEGRAM_BOT_TOKEN = config.telegram_bot_token
GOOGLE_API_KEY = config.google_api_key
ALLOWED_USERS_ENV = config.allowed_users_env
WEBHOOK_URL = config.webhook_url
PORT = config.port
REDIS_URL = config.redis_url

# Build a set of allowed user IDs for white-listing access
ALLOWED_USERS = set()
for u in ALLOWED_USERS_ENV.split(","):
    if u.strip().isdigit():
        ALLOWED_USERS.add(int(u.strip()))

if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
    logging.error("TELEGRAM_BOT_TOKEN or GOOGLE_API_KEY not found in .env")
    sys.exit(1)

# Initialize Aiogram instances with default HTML parsing
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Initialize Google Gemini Client
gemini_client = genai.Client(api_key=GOOGLE_API_KEY, http_options={"api_version": "v1alpha"})

# ==========================================
# STATE STORAGE (FSM) INITIALIZATION
# ==========================================
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        storage = RedisStorage(redis=redis_client)
        logging.info("Redis successfully connected for FSM storage.")
    except Exception as e:
        logging.error(f"Error connecting to Redis: {e}")
        redis_client = None
        storage = MemoryStorage()
        logging.info("Fallback: Using in-memory FSM storage (Warning: Data clears on restart).")
else:
    redis_client = None
    storage = MemoryStorage()
    logging.info("REDIS_URL not found, using in-memory FSM storage.")

dp = Dispatcher(storage=storage)


# ==========================================
# CONSTANTS & FSM STATES
# ==========================================
class BotStates(StatesGroup):
    WAITING_FOR_LANGUAGE = State()       # Bot expects language selection
    WAITING_FOR_IMAGE_PROMPT = State()   # Bot expects a description for generating an image
    WAITING_FOR_PHOTO_TO_EDIT = State()  # Bot expects a photo to edit
    WAITING_FOR_EDIT_PROMPT = State()    # Bot received the photo and is waiting for text instructions on how to edit

# Button text matching lists (used for command routing)
BTN_GENERATE_LIST = [TEXTS["EN"]["BTN_GENERATE"], TEXTS["RU"]["BTN_GENERATE"]]
BTN_EDIT_LIST = [TEXTS["EN"]["BTN_EDIT"], TEXTS["RU"]["BTN_EDIT"]]
BTN_HELP_LIST = [TEXTS["EN"]["BTN_HELP"], TEXTS["RU"]["BTN_HELP"]]
BTN_PRO_LIST = [TEXTS["EN"]["BTN_PRO"], TEXTS["RU"]["BTN_PRO"]]
BTN_FLASH_LIST = [TEXTS["EN"]["BTN_FLASH"], TEXTS["RU"]["BTN_FLASH"]]
BTN_LANG_LIST = [TEXTS["EN"]["BTN_LANG"], TEXTS["RU"]["BTN_LANG"]]


# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def format_html_response(text: str) -> str:
    """Utility function: Escapes user text and converts basic Markdown to Telegram HTML tags"""
    text = html.escape(text, quote=False)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text

async def get_main_keyboard(state: FSMContext) -> ReplyKeyboardMarkup:
    """Dynamically build the main keyboard based on language and active mode."""
    data = await state.get_data()
    lang = data.get("lang", "EN")
    current_mode = data.get("mode", "FLASH")
    
    t = TEXTS[lang]
    mode_btn = t["BTN_PRO"] if current_mode == "FLASH" else t["BTN_FLASH"]
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["BTN_GENERATE"]), KeyboardButton(text=t["BTN_EDIT"])],
            [KeyboardButton(text=mode_btn)],
            [KeyboardButton(text=t["BTN_LANG"]), KeyboardButton(text=t["BTN_HELP"])]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_lang_keyboard() -> ReplyKeyboardMarkup:
    """Builds the language selection keyboard"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="English 🇬🇧"), KeyboardButton(text="Русский 🇷🇺")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard


# ==========================================
# MIDDLEWARES
# ==========================================
@dp.message.outer_middleware()
async def access_control_middleware(handler, event: Message, data: dict):
    """Access Blocker: Filters out messages from users not listed in the ALLOWED_USERS whitelist"""
    if ALLOWED_USERS and event.from_user.id not in ALLOWED_USERS:
        logging.warning(f"Action: access_denied | UserID: {event.from_user.id} | Reason: not_in_whitelist")
        return
    return await handler(event, data)


# ==========================================
# LANGUAGE SELECTION HANDLERS
# ==========================================
@dp.message(F.text.in_(BTN_LANG_LIST))
async def command_change_lang(message: Message, state: FSMContext):
    """Triggered when the user wants to change their language"""
    await state.set_state(BotStates.WAITING_FOR_LANGUAGE)
    
    data = await state.get_data()
    lang = data.get("lang", "EN")
    t = TEXTS[lang]
    
    await message.answer(t["CHOOSE_LANG"], reply_markup=get_lang_keyboard())

@dp.message(BotStates.WAITING_FOR_LANGUAGE, F.text.in_(["English 🇬🇧", "Русский 🇷🇺"]))
async def handle_language_selection(message: Message, state: FSMContext):
    """Saves the chosen language to state and shows the main menu"""
    lang = "EN" if "English" in message.text else "RU"
    await state.update_data(lang=lang)
    await state.set_state(None)
    
    t = TEXTS[lang]
    kb = await get_main_keyboard(state)
    await message.answer(t["LANG_SET"], reply_markup=kb)
    await message.answer(t["WELCOME"], reply_markup=kb)

@dp.message(BotStates.WAITING_FOR_LANGUAGE)
async def handle_invalid_language(message: Message, state: FSMContext):
    """Fallback if user types something invalid during language selection"""
    await message.answer("Please choose a language from the keyboard below.\nПожалуйста, выберите язык на клавиатуре ниже.", reply_markup=get_lang_keyboard())


# ==========================================
# COMMAND & BUTTON HANDLERS
# ==========================================
@dp.message(CommandStart())
async def command_start(message: Message, state: FSMContext):
    """Entry point: Reset FSM, default to FLASH, and ask for language if not set"""
    data = await state.get_data()
    lang = data.get("lang")
    
    await state.clear()
    await state.update_data(mode="FLASH")
    logging.info(f"Action: command_start | UserID: {message.from_user.id}")
    
    if not lang:
        await state.set_state(BotStates.WAITING_FOR_LANGUAGE)
        await message.answer(TEXTS["EN"]["CHOOSE_LANG"], reply_markup=get_lang_keyboard())
    else:
        # User already has a language, just show the welcome text
        await state.update_data(lang=lang)
        t = TEXTS[lang]
        kb = await get_main_keyboard(state)
        await message.answer(t["WELCOME"], reply_markup=kb)

@dp.message(F.text.in_(BTN_GENERATE_LIST))
async def handle_generate_image_command(message: Message, state: FSMContext):
    """Initiate the image generation process"""
    await state.set_state(BotStates.WAITING_FOR_IMAGE_PROMPT)
    logging.info(f"Action: command_generate_image | UserID: {message.from_user.id}")
    
    data = await state.get_data()
    t = TEXTS[data.get("lang", "EN")]
    
    kb = await get_main_keyboard(state)
    await message.answer(t["GENERATE_PROMPT"], reply_markup=kb)

@dp.message(F.text.in_(BTN_EDIT_LIST))
async def handle_edit_image_command(message: Message, state: FSMContext):
    """Initiate the photo editing process"""
    await state.set_state(BotStates.WAITING_FOR_PHOTO_TO_EDIT)
    logging.info(f"Action: command_edit_image | UserID: {message.from_user.id}")
    
    data = await state.get_data()
    t = TEXTS[data.get("lang", "EN")]
    
    kb = await get_main_keyboard(state)
    await message.answer(t["EDIT_PROMPT"], reply_markup=kb)

@dp.message(F.text.in_(BTN_HELP_LIST))
async def command_help(message: Message, state: FSMContext):
    """Display quick reference information about the bot"""
    await state.set_state(None)
    logging.info(f"Action: command_help | UserID: {message.from_user.id}")
    
    data = await state.get_data()
    t = TEXTS[data.get("lang", "EN")]
    
    kb = await get_main_keyboard(state)
    await message.answer(t["HELP_TEXT"], reply_markup=kb)

@dp.message(F.text.in_(BTN_PRO_LIST))
async def command_mode_pro(message: Message, state: FSMContext):
    """Switch to PRO Mode: Activates heavier Gemini models"""
    await state.update_data(mode="PRO")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: PRO")
    
    data = await state.get_data()
    t = TEXTS[data.get("lang", "EN")]
    
    kb = await get_main_keyboard(state)
    await message.answer(t["PRO_ACTIVATED"], reply_markup=kb)

@dp.message(F.text.in_(BTN_FLASH_LIST))
async def command_mode_flash(message: Message, state: FSMContext):
    """Switch to FLASH Mode: Activates lightweight and rapid models"""
    await state.update_data(mode="FLASH")
    logging.info(f"Action: mode_switch | UserID: {message.from_user.id} | Mode: FLASH")
    
    data = await state.get_data()
    t = TEXTS[data.get("lang", "EN")]
    
    kb = await get_main_keyboard(state)
    await message.answer(t["FLASH_ACTIVATED"], reply_markup=kb)


# ==========================================
# GEMINI API INTERACTION
# ==========================================
async def handle_genai_error(e: APIError, status_msg: Message, lang: str):
    """Handles common Gemini API errors and updates the status message for the user"""
    t = TEXTS[lang]
    if e.code == 400:
        await status_msg.edit_text(t["ERR_SAFETY"])
    elif e.code == 429:
        await status_msg.edit_text(t["ERR_RATELIMIT"])
    elif e.code >= 500:
        await status_msg.edit_text(t["ERR_SERVER"])
    else:
        await status_msg.edit_text(t["ERR_UNKNOWN"].format(error=e.message))

async def generate_image_from_text(prompt: str, mode: str, status_msg: Message, lang: str) -> bytes | None:
    """Generates an image from scratch based on a text prompt"""
    model_name = IMAGE_GEN_MODELS.get(mode, IMAGE_GEN_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: generate_image | Model: {model_name}")
    t = TEXTS[lang]
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
        await handle_genai_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: generate_image | Model: {model_name} | Error: {e}")
        await status_msg.edit_text(t["ERR_GEN_INTERNAL"])
        return None

async def edit_image_with_prompt(image_bytes: bytes, prompt: str, mode: str, status_msg: Message, lang: str) -> bytes | None:
    """Edits an existing image strictly according to the user's prompt"""
    model_name = IMAGE_EDIT_MODELS.get(mode, IMAGE_EDIT_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: edit_image | Model: {model_name}")
    t = TEXTS[lang]
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
        await handle_genai_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: edit_image | Model: {model_name} | Error: {e}")
        await status_msg.edit_text(t["ERR_EDIT_INTERNAL"])
        return None

async def transcribe_audio(audio_bytes: bytes, mode: str, status_msg: Message, lang: str) -> str | None:
    """Converts a voice message into text using Gemini text/audio models"""
    model_name = TEXT_AUDIO_MODELS.get(mode, TEXT_AUDIO_MODELS["FLASH"])[0]
    logging.info(f"Action: api_call | Type: transcribe_audio | Model: {model_name}")
    t = TEXTS[lang]
    
    prompt_lang = "Transcribe this voice message to text. Only return the recognized text without any extra words."
    if lang == "RU":
        prompt_lang = "Транскрибируй это голосовое сообщение в текст. Выведи только распознанный текст без лишних слов."
        
    contents = [
        genai_types.Part.from_bytes(data=audio_bytes, mime_type='audio/ogg'),
        prompt_lang
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
        await handle_genai_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: transcribe_audio | Model: {model_name} | Error: {e}")
        await status_msg.edit_text(t["ERR_AUDIO_TRANS"])
        return None


# ==========================================
# INPUT DATA PROCESSING (TEXT/VOICE/PHOTO)
# ==========================================
async def process_text_or_voice_prompt(text: str, message: Message, bot: Bot, state: FSMContext, status_msg: Message | None = None):
    """
    Unified logic for processing finalized text text details:
    Accepts ready text (whether typed or transcribed from voice) and routes it to the appropriate API function.
    """
    current_state = await state.get_state()
    data = await state.get_data()
    mode = data.get("mode", "FLASH")
    lang = data.get("lang", "EN")
    t = TEXTS[lang]
    
    # Image Generation Flow
    if current_state == BotStates.WAITING_FOR_IMAGE_PROMPT.state:
        logging.info(f"Action: start_art_generation | UserID: {message.from_user.id} | Prompt: {text}")
        if not status_msg:
            status_msg = await message.answer(t["PROCESS_GEN_START"])
        else:
            await status_msg.edit_text(t["PROCESS_GEN_START"])
            
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        image_bytes = await generate_image_from_text(text, mode, status_msg, lang)
        
        if image_bytes:
            await message.answer_photo(types.BufferedInputFile(image_bytes, filename="art.jpg"))
            await state.set_state(None)
            await status_msg.delete()
            logging.info(f"Action: success_art | UserID: {message.from_user.id}")
        
    # Image Editing Flow
    elif current_state == BotStates.WAITING_FOR_EDIT_PROMPT.state:
        edit_file_id = data.get("edit_photo_file_id")
        if not edit_file_id:
            msg = t["ERR_LOAD_EDIT"]
            if status_msg:
                await status_msg.edit_text(msg)
            else:
                await message.answer(msg)
            await state.set_state(None)
            return

        logging.info(f"Action: start_edit_generation | UserID: {message.from_user.id} | Prompt: {text}")

        if not status_msg:
            status_msg = await message.answer(t["PROCESS_EDIT_PREP"])
        else:
            await status_msg.edit_text(t["PROCESS_EDIT_PREP"])
            
        # Download the photo just in time right before API request to save memory footprint
        try:
            file = await bot.get_file(edit_file_id)
            downloaded_file = await bot.download_file(file.file_path)
            image_bytes = downloaded_file.read()
            
            await status_msg.edit_text(t["PROCESS_EDIT_GEN"])
            await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            
            edited_image_bytes = await edit_image_with_prompt(image_bytes, text, mode, status_msg, lang)
            
            if edited_image_bytes:
                await message.answer_photo(types.BufferedInputFile(edited_image_bytes, filename="edited.jpg"))
                await state.set_state(None)
                await state.update_data(edit_photo_file_id=None)
                await status_msg.delete()
                logging.info(f"Action: success_edit | UserID: {message.from_user.id}")
        except Exception as e:
            logging.error(f"Action: error_download_edit | UserID: {message.from_user.id} | Error: {e}")
            await status_msg.edit_text(t["ERR_DL_TELEGRAM"])

    # Prevent submitting text when the bot expects a photo upload
    elif current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        msg = t["ERR_NEED_PHOTO_NOT_TEXT"]
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)

    # General fallback for text
    else:
        msg = t["ERR_MENU_FIRST"]
        if status_msg:
            await status_msg.edit_text(msg)
        else:
            await message.answer(msg)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot, state: FSMContext):
    """Route regular text directly to the unified processing function"""
    await process_text_or_voice_prompt(message.text, message, bot, state)

@dp.message(F.voice)
async def handle_user_voice(message: Message, bot: Bot, state: FSMContext):
    """Voice handler: downloads voice, transcribes it, and routes to unified logic"""
    current_state = await state.get_state()
    data = await state.get_data()
    lang = data.get("lang", "EN")
    t = TEXTS[lang]

    # Prevent trying to describe a photo using voice when waiting for photo upload
    if current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        await message.answer(t["VOICE_NO_PHOTO"])
        return
        
    # Prevent voice interactions when idle
    if not current_state:
        await message.answer(t["ERR_MENU_FIRST"])
        return

    logging.info(f"Action: receive_voice | UserID: {message.from_user.id}")
    status_msg = await message.answer(t["PROCESS_VOICE_RX"])
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        file_id = message.voice.file_id
        file = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file.file_path)
        audio_bytes = downloaded_file.read()

        mode = data.get("mode", "FLASH")
        
        await status_msg.edit_text(t["PROCESS_VOICE_TRANS"])
        text = await transcribe_audio(audio_bytes, mode, status_msg, lang)

        if text:
            # Display safely encoded transcription copy for validation
            safe_text = format_html_response(text)
            await message.answer(t["TXT_TRANSCRIBED"].format(text=safe_text))
            await process_text_or_voice_prompt(text, message, bot, state, status_msg)
            
    except Exception as e:
        logging.error(f"Action: error_voice_handling | UserID: {message.from_user.id} | Error: {e}")
        await status_msg.edit_text(t["ERR_VOICE_DL"])

@dp.message(F.photo)
async def handle_user_photo(message: Message, bot: Bot, state: FSMContext):
    """Processes newly uploaded photos"""
    current_state = await state.get_state()
    data = await state.get_data()
    lang = data.get("lang", "EN")
    t = TEXTS[lang]
    
    # State matches the Edit photo intention
    if current_state == BotStates.WAITING_FOR_PHOTO_TO_EDIT.state:
        file_id = message.photo[-1].file_id
        
        # We only save file_id within Redis/In-Memory contexts to prevent state overflow
        await state.update_data(edit_photo_file_id=file_id)
        await state.set_state(BotStates.WAITING_FOR_EDIT_PROMPT)
        logging.info(f"Action: receive_photo_for_edit | UserID: {message.from_user.id}")
        
        await message.answer(t["PHOTO_LOADED_PROMPT"])
        
    # Guard if the user uploads ANOTHER photo inside the prompt state
    elif current_state == BotStates.WAITING_FOR_EDIT_PROMPT.state:
         await message.answer(t["PHOTO_ALREADY_RX"])

    # Error guard: Generative workflow only supports prompts
    elif current_state == BotStates.WAITING_FOR_IMAGE_PROMPT.state:
        await message.answer(t["ERR_PHOTO_IN_GEN"])
        
    # Standard fallback
    else:
        await message.answer(t["ERR_PHOTO_NO_MENU"])

@dp.message()
async def handle_other_media(message: Message, state: FSMContext):
    """Fallback handler for unsupported documents: files, stickers, videos"""
    data = await state.get_data()
    lang = data.get("lang", "EN")
    t = TEXTS[lang]
    await message.answer(t["ERR_UNSUPPORTED_MEDIA"])


# ==========================================
# ENTRYPOINT AND BOOTSTRAPPING
# ==========================================
async def main():
    """Main function bootstraps aiogram configuring Webhooks or Long Polling"""
    if WEBHOOK_URL:
        logging.info(f"Starting bot through Webhook on port {PORT}")
        app = web.Application()
        # Secret tokens for Telegram verification tolerate strictly A-Z, a-z, 0-9, _, and -
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
            
            # Application main loop
            while True:
                await asyncio.sleep(3600)
        finally:
            if redis_client:
                await redis_client.aclose()
    else:
        logging.info("Initializing local long polling...")
        await bot.delete_webhook(drop_pending_updates=True) # Cleans up stalled webhook bindings safely
        try:
            await dp.start_polling(bot)
        finally:
            if redis_client:
                await redis_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot successfully stopped by administrator.")
