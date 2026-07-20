import asyncio
import base64
import logging
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
from aiohttp import ClientSession, ClientTimeout, FormData, web
from dotenv import load_dotenv
import redis.asyncio as redis

from config import (
    IMAGE_MODELS,
    TRANSCRIPTION_MODELS,
    load_config,
)
from texts import TEXTS

# Clean up verbose third-party logger output
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Load environment variables
load_dotenv()

# Read main configurations
config = load_config()
TELEGRAM_BOT_TOKEN = config.telegram_bot_token
OPENAI_API_KEY = config.openai_api_key
XAI_API_KEY = config.xai_api_key
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

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY or not XAI_API_KEY:
    logging.error("TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, or XAI_API_KEY not found in .env")
    sys.exit(1)
if not GOOGLE_API_KEY:
    logging.warning("GOOGLE_API_KEY not found: Google fallback is disabled")

# Initialize Aiogram instances with default HTML parsing
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

OPENAI_API_URL = "https://api.openai.com/v1"
XAI_API_URL = "https://api.x.ai/v1"
GOOGLE_API_URL = "https://generativelanguage.googleapis.com/v1"
API_TIMEOUT = ClientTimeout(total=180)

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
    """Switch to PRO Mode: Activates the highest-quality image model"""
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
# IMAGE AND AUDIO PROVIDER INTERACTION
# ==========================================
class ProviderAPIError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


async def handle_provider_error(e: ProviderAPIError, status_msg: Message, lang: str):
    """Map provider HTTP errors to the existing localized bot messages."""
    t = TEXTS[lang]
    if e.code in {"moderation_blocked", "content_policy_violation"}:
        await status_msg.edit_text(t["ERR_SAFETY"])
    elif e.status == 429:
        await status_msg.edit_text(t["ERR_RATELIMIT"])
    elif e.status >= 500:
        await status_msg.edit_text(t["ERR_SERVER"])
    else:
        await status_msg.edit_text(t["ERR_UNKNOWN"].format(error=e.message))


def _error_from_payload(status: int, payload: dict) -> ProviderAPIError:
    error = payload.get("error", payload)
    if isinstance(error, dict):
        return ProviderAPIError(
            status,
            str(error.get("message", "Provider request failed")),
            error.get("code") or error.get("type"),
        )
    return ProviderAPIError(status, str(error))


async def _response_json(response) -> dict:
    try:
        payload = await response.json(content_type=None)
    except Exception:
        payload = {"error": {"message": (await response.text())[:500]}}
    if response.status >= 400:
        raise _error_from_payload(response.status, payload)
    return payload


async def _image_bytes_from_payload(session: ClientSession, payload: dict) -> bytes | None:
    images = payload.get("data") or []
    if not images:
        return None
    image = images[0]
    if image.get("b64_json"):
        return base64.b64decode(image["b64_json"])
    if image.get("url"):
        async with session.get(image["url"]) as response:
            if response.status >= 400:
                raise ProviderAPIError(response.status, "Could not download generated image")
            return await response.read()
    return None


async def _openai_generate(session: ClientSession, prompt: str, model: dict) -> bytes | None:
    payload = {
        "model": model["model"],
        "prompt": prompt,
        "quality": model["quality"],
        "size": model["size"],
        "output_format": model["output_format"],
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    async with session.post(f"{OPENAI_API_URL}/images/generations", json=payload, headers=headers) as response:
        return await _image_bytes_from_payload(session, await _response_json(response))


async def _openai_edit(session: ClientSession, image_bytes: bytes, prompt: str, model: dict) -> bytes | None:
    form = FormData()
    form.add_field("model", model["model"])
    form.add_field("prompt", prompt)
    form.add_field("quality", model["quality"])
    form.add_field("size", model["size"])
    form.add_field("output_format", model["output_format"])
    form.add_field("image[]", image_bytes, filename="source.jpg", content_type="image/jpeg")
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    async with session.post(f"{OPENAI_API_URL}/images/edits", data=form, headers=headers) as response:
        return await _image_bytes_from_payload(session, await _response_json(response))


async def _xai_generate(session: ClientSession, prompt: str, model: dict) -> bytes | None:
    payload = {
        "model": model["model"],
        "prompt": prompt,
        "resolution": model["resolution"],
        "response_format": "b64_json",
    }
    headers = {"Authorization": f"Bearer {XAI_API_KEY}"}
    async with session.post(f"{XAI_API_URL}/images/generations", json=payload, headers=headers) as response:
        return await _image_bytes_from_payload(session, await _response_json(response))


async def _xai_edit(session: ClientSession, image_bytes: bytes, prompt: str, model: dict) -> bytes | None:
    image_data = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model["model"],
        "prompt": prompt,
        "image": {
            "url": f"data:image/jpeg;base64,{image_data}",
            "type": "image_url",
        },
    }
    headers = {"Authorization": f"Bearer {XAI_API_KEY}"}
    async with session.post(f"{XAI_API_URL}/images/edits", json=payload, headers=headers) as response:
        return await _image_bytes_from_payload(session, await _response_json(response))


def _google_image_from_payload(payload: dict) -> bytes | None:
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline_data = part.get("inlineData") or part.get("inline_data")
            if inline_data and inline_data.get("data"):
                return base64.b64decode(inline_data["data"])
    return None


async def _google_generate(session: ClientSession, prompt: str, model: dict) -> bytes | None:
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"x-goog-api-key": GOOGLE_API_KEY}
    url = f"{GOOGLE_API_URL}/models/{model['model']}:generateContent"
    async with session.post(url, json=payload, headers=headers) as response:
        return _google_image_from_payload(await _response_json(response))


async def _google_edit(session: ClientSession, image_bytes: bytes, prompt: str, model: dict) -> bytes | None:
    payload = {
        "contents": [{
            "parts": [
                {
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                },
                {"text": prompt},
            ]
        }]
    }
    headers = {"x-goog-api-key": GOOGLE_API_KEY}
    url = f"{GOOGLE_API_URL}/models/{model['model']}:generateContent"
    async with session.post(url, json=payload, headers=headers) as response:
        return _google_image_from_payload(await _response_json(response))


def _is_safety_error(error: ProviderAPIError) -> bool:
    return error.code in {"moderation_blocked", "content_policy_violation", "SAFETY"}


async def _generate_with_provider(session: ClientSession, prompt: str, model: dict) -> bytes | None:
    if model["provider"] == "openai":
        return await _openai_generate(session, prompt, model)
    if model["provider"] == "xai":
        return await _xai_generate(session, prompt, model)
    return await _google_generate(session, prompt, model)


async def _edit_with_provider(
    session: ClientSession,
    image_bytes: bytes,
    prompt: str,
    model: dict,
) -> bytes | None:
    if model["provider"] == "openai":
        return await _openai_edit(session, image_bytes, prompt, model)
    if model["provider"] == "xai":
        return await _xai_edit(session, image_bytes, prompt, model)
    return await _google_edit(session, image_bytes, prompt, model)


async def generate_image_from_text(prompt: str, mode: str, status_msg: Message, lang: str) -> bytes | None:
    """Generates an image from scratch based on a text prompt"""
    models = IMAGE_MODELS.get(mode, IMAGE_MODELS["FLASH"])
    t = TEXTS[lang]
    last_error = None
    try:
        async with ClientSession(timeout=API_TIMEOUT) as session:
            for index, model in enumerate(models):
                if model["provider"] == "google" and not GOOGLE_API_KEY:
                    continue
                logging.info(
                    f"Action: api_call | Type: generate_image | Provider: {model['provider']} | Model: {model['model']}"
                )
                try:
                    image = await _generate_with_provider(session, prompt, model)
                    if image:
                        return image
                    last_error = RuntimeError("Provider returned no image")
                except ProviderAPIError as e:
                    if _is_safety_error(e):
                        raise
                    last_error = e
                except Exception as e:
                    last_error = e
                if index < len(models) - 1:
                    logging.warning(
                        f"Action: provider_fallback | Type: generate_image | Failed: {model['provider']} | Error: {last_error}"
                    )
            if last_error:
                raise last_error
            return None
    except ProviderAPIError as e:
        logging.error(f"Action: api_error | Type: generate_image | Error: {e.message}")
        await handle_provider_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: generate_image | Error: {e}")
        await status_msg.edit_text(t["ERR_GEN_INTERNAL"])
        return None


async def edit_image_with_prompt(image_bytes: bytes, prompt: str, mode: str, status_msg: Message, lang: str) -> bytes | None:
    """Edits an existing image strictly according to the user's prompt"""
    models = IMAGE_MODELS.get(mode, IMAGE_MODELS["FLASH"])
    t = TEXTS[lang]
    last_error = None
    try:
        async with ClientSession(timeout=API_TIMEOUT) as session:
            for index, model in enumerate(models):
                if model["provider"] == "google" and not GOOGLE_API_KEY:
                    continue
                logging.info(
                    f"Action: api_call | Type: edit_image | Provider: {model['provider']} | Model: {model['model']}"
                )
                try:
                    image = await _edit_with_provider(session, image_bytes, prompt, model)
                    if image:
                        return image
                    last_error = RuntimeError("Provider returned no image")
                except ProviderAPIError as e:
                    if _is_safety_error(e):
                        raise
                    last_error = e
                except Exception as e:
                    last_error = e
                if index < len(models) - 1:
                    logging.warning(
                        f"Action: provider_fallback | Type: edit_image | Failed: {model['provider']} | Error: {last_error}"
                    )
            if last_error:
                raise last_error
            return None
    except ProviderAPIError as e:
        logging.error(f"Action: api_error | Type: edit_image | Error: {e.message}")
        await handle_provider_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: edit_image | Error: {e}")
        await status_msg.edit_text(t["ERR_EDIT_INTERNAL"])
        return None


async def transcribe_audio(audio_bytes: bytes, mode: str, status_msg: Message, lang: str) -> str | None:
    """Converts a voice message into text with a Google fallback."""
    t = TEXTS[lang]
    last_error = None
    try:
        async with ClientSession(timeout=API_TIMEOUT) as session:
            for index, model in enumerate(TRANSCRIPTION_MODELS):
                if model["provider"] == "google" and not GOOGLE_API_KEY:
                    continue
                logging.info(
                    f"Action: api_call | Type: transcribe_audio | Provider: {model['provider']} | Model: {model['model']}"
                )
                try:
                    if model["provider"] == "openai":
                        form = FormData()
                        form.add_field("model", model["model"])
                        form.add_field("response_format", "json")
                        form.add_field("file", audio_bytes, filename="voice.ogg", content_type="audio/ogg")
                        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
                        async with session.post(
                            f"{OPENAI_API_URL}/audio/transcriptions",
                            data=form,
                            headers=headers,
                        ) as response:
                            payload = await _response_json(response)
                            text = payload.get("text")
                    else:
                        prompt = "Transcribe this voice message. Return only the recognized text."
                        payload = {
                            "contents": [{
                                "parts": [
                                    {
                                        "inlineData": {
                                            "mimeType": "audio/ogg",
                                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                                        }
                                    },
                                    {"text": prompt},
                                ]
                            }]
                        }
                        headers = {"x-goog-api-key": GOOGLE_API_KEY}
                        url = f"{GOOGLE_API_URL}/models/{model['model']}:generateContent"
                        async with session.post(url, json=payload, headers=headers) as response:
                            result = await _response_json(response)
                            parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                            text = next((part.get("text") for part in parts if part.get("text")), None)
                    if text:
                        return text.strip()
                    last_error = RuntimeError("Provider returned no transcription")
                except ProviderAPIError as e:
                    if _is_safety_error(e):
                        raise
                    last_error = e
                except Exception as e:
                    last_error = e
                if index < len(TRANSCRIPTION_MODELS) - 1:
                    logging.warning(
                        f"Action: provider_fallback | Type: transcribe_audio | Failed: {model['provider']} | Error: {last_error}"
                    )
            if last_error:
                raise last_error
            return None
    except ProviderAPIError as e:
        logging.error(f"Action: api_error | Type: transcribe_audio | Error: {e.message}")
        await handle_provider_error(e, status_msg, lang)
        return None
    except Exception as e:
        logging.error(f"Action: system_error | Type: transcribe_audio | Error: {e}")
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
