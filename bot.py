import asyncio, logging, os, sys, textwrap, io
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
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

# Smart Cascades: Best models vs Fast models
CASCADES = {
    'pro': {
        'text': ['gemini-2.5-pro', 'gemini-1.5-pro'],
        'image': ['imagen-3.0-generate-002', 'imagen-3.0-generate-001'],
        'edit': ['imagen-3.0-generate-002', 'imagen-3.0-generate-001']
    },
    'flash': {
        'text': ['gemini-2.5-flash', 'gemini-1.5-flash'],
        'image': ['imagen-3.0-fast-001', 'imagen-3.0-generate-001'],
        'edit': ['imagen-3.0-fast-001', 'imagen-3.0-generate-001']
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
            logging.warning(f"Unauthorized access attempt: ID {user.id if user else 'Unknown'}")
            if isinstance(event, dict): 
                return
            msg = data.get("event_update").message
            if msg:
                await msg.reply(
                    f"⛔️ **Доступ запрещен!**\n\n"
                    f"Извините, но этот бот является приватным. Вы не состоите в белом списке.\n"
                    f"Ваш Telegram ID: `{user.id}`\n\n"
                    f"Передайте этот ID администратору бота, чтобы он добавил вас в список доступа."
                )
            return
        return await handler(event, data)

dp.message.middleware(AuthMiddleware())
dp.include_router(router)

class BotStates(StatesGroup):
    waiting_for_gen_prompt = State()
    waiting_for_edit_photo = State()
    waiting_for_edit_prompt = State()

# --- KEYBOARDS ---
def get_main_kb(user_id: int) -> ReplyKeyboardMarkup:
    current_mode = USER_MODES.get(user_id, 'flash')
    mode_btn_text = "💎 Режим: PRO (Лучшее качество)" if current_mode == 'pro' else "🚀 Режим: FLASH (Оптимальный)"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎨 Сгенерировать картинку"), KeyboardButton(text="🪄 Изменить фото")],
            [KeyboardButton(text=mode_btn_text)],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True
)

# --- LOGIC ---
async def generate_with_fallback(models_list: list[str], contents, is_image: bool = False, image_bytes: bytes | None = None, edit_mode: bool = False):
    last_err = None
    for model in models_list:
        try:
            if edit_mode and image_bytes:
                # Use edit_image capability
                raw_ref_image = types.RawReferenceImage(
                    reference_id=1,
                    reference_image=types.Image(image_bytes=image_bytes, mime_type="image/jpeg"),
                )
                return await client.aio.models.edit_image(
                    model=model,
                    prompt=contents,
                    reference_images=[raw_ref_image],
                    config=types.EditImageConfig(
                        edit_mode="EDIT_MODE_DEFAULT",
                        output_mime_type="image/jpeg"
                    )
                )

            elif is_image:
                return await client.aio.models.generate_images(
                    model=model,
                    prompt=contents[0] if isinstance(contents, list) else contents,
                    config=types.GenerateImagesConfig(
                        # safety_settings are not supported by the generate_images API yet!
                        output_mime_type="image/jpeg",
                        aspect_ratio="1:1"
                    )
                )
            else:
                # Text / Multimodal
                return await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(safety_settings=DEFAULT_SAFETY)
                )

        except APIError as e:
            last_err = e
            logging.warning(f"Model {model} API error: {e}")
            if any(code in str(e) for code in ["429", "503", "500", "400"]): 
                continue
            break
        except Exception as e:
            last_err = e
            logging.error(f"Unexpected error with {model}: {e}")
            break
            
    if last_err is not None:
        raise last_err
    raise Exception("Серверы перегружены, попробуйте чуть позже.")

async def handle_response(message: Message, response, is_image: bool = False):
    if is_image:
        if hasattr(response, 'generated_images') and response.generated_images:
            img_obj = response.generated_images[0].image
            img_data = getattr(img_obj, 'image_bytes', getattr(img_obj, 'data', None))
            if img_data:
                await message.reply_photo(photo=BufferedInputFile(img_data, filename="result.jpg"))
                return True
        await message.reply("❌ Не удалось сгенерировать изображение. Возможно, ваш промпт был отклонен фильтрами безопасности.")
        return False
    else:
        text = None
        if hasattr(response, 'text') and response.text:
            text = response.text
        elif hasattr(response, 'candidates') and response.candidates:
            parts = response.candidates[0].content.parts
            if parts:
                text = parts[0].text
                
        if not text:
            await message.reply("⚠️ Бот сгенерировал пустой ответ. Это происходит когда Google блокирует контент по соображениям безопасности.")
            return False
            
        for chunk in textwrap.wrap(text, width=4000):
            try:
                await message.answer(chunk)
            except Exception:
                await message.answer(chunk, parse_mode=None)
    return True

async def download_media(file_id: str) -> io.BytesIO:
    file = await bot.get_file(file_id)
    out = io.BytesIO()
    await bot.download_file(file.file_path, out)
    out.seek(0)
    return out

# --- HANDLERS ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    USER_MODES.setdefault(message.from_user.id, 'flash')
    await message.answer(
        "👋 **Добро пожаловать в ИИ-ассистент на базе Google Gemini!**\n\n"
        "Я умею отвечать на вопросы, переводить, писать код, распознавать голосовые сообщения, описывать любые фотографии и рисовать картинки.\n\n"
        "Для простого общения — просто напишите мне текст, отправьте голосовое или фото с текстом.\n"
        "Вы можете использовать меню ниже для создания изображений или переключения мощности нейросети.", 
        reply_markup=get_main_kb(message.from_user.id)
    )

@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "💡 **Как мной пользоваться:**\n\n"
        "✉️ **Текст:** Просто отправьте любой текст, и я на него отвечу.\n"
        "🎤 **Аудио/Голос:** Отправьте мне голосовое сообщение, и я его расшифрую (и отвечу на вопрос внутри).\n"
        "👀 **Фотографии:** Отправьте любую фото, и я расскажу что на ней. Вы можете добавить подпись-инструкцию к фото (например: \"переведи этот текст в формат Excel\").\n"
        "🎨 **Создать картинку:** Нажмите 'Сгенерировать картинку' и опишите то, что хотите увидеть.\n"
        "🪄 **Изменить фото:** Нажмите 'Изменить фото', чтобы нейросеть проанализировала и креативно дописала или изменила вашу картинку.\n\n"
        "**О переключателе режимов:**\n"
        "• **Режим FLASH** 🚀 — быстрый, умный, подходит для обычных задач. Экономит ресурсы.\n"
        "• **Режим PRO** 💎 — использует самую мощную модель. Используйте для сложных расчетов и детальных картинок.",
        reply_markup=get_main_kb(message.from_user.id)
    )

@router.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Жду ваших команд!", reply_markup=get_main_kb(message.from_user.id))

@router.message(F.text.in_(["💎 Режим: PRO (Лучшее качество)", "🚀 Режим: FLASH (Оптимальный)"]))
async def toggle_mode(message: Message):
    current = USER_MODES.get(message.from_user.id, 'flash')
    new_mode = 'pro' if current == 'flash' else 'flash'
    USER_MODES[message.from_user.id] = new_mode
    
    mode_name = "💎 PRO-режим (Максимальное качество)" if new_mode == 'pro' else "🚀 FLASH-режим (Баланс скорости и качества)"
    await message.answer(f"✅ Включен **{mode_name}**.", reply_markup=get_main_kb(message.from_user.id))

# --- IMAGE GENERATION ---
@router.message(F.text == "🎨 Сгенерировать картинку")
async def btn_gen(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_gen_prompt)
    await message.answer(
        "📝 Опишите картинку, которую хотите получить. Например:\n"
        "_«Милый рыжий кот пьет кофе в киберпанк городе»_", 
        reply_markup=cancel_kb,
        parse_mode="Markdown"
    )

@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen(message: Message, state: FSMContext):
    status = await message.reply("🎨 Рисую изображение... Это займет несколько секунд.")
    try:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['image'], contents=message.text, is_image=True)
        if await handle_response(message, resp, is_image=True):
            await state.clear()
            await message.answer("✨ Изображение готово!", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await message.reply(f"❌ Ой, произошла ошибка генерации: `{e}`", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    finally:
        await status.delete()

# --- IMAGE EDITING ---
@router.message(F.text == "🪄 Изменить фото")
async def btn_edit(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_edit_photo)
    await message.answer(
        "🖼 Отправьте мне фото, которое мы будем анализировать и менять при помощи нейросети.", 
        reply_markup=cancel_kb
    )

@router.message(BotStates.waiting_for_edit_photo, F.photo)
async def handle_edit_photo(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1]
        media_stream = await download_media(photo.file_id)
        
        await state.update_data(photo_data=media_stream.read())
        await state.set_state(BotStates.waiting_for_edit_prompt)
        await message.answer(
            "📝 Отлично! Теперь напишите, что сделать с этой картинкой. Например:\n"
            "_«Одень человека на фото в шляпу»_ или _«Сделай фон зимним лесом»_",
            reply_markup=cancel_kb,
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.reply(f"❌ Проблема с загрузкой фото: `{e}`")
        await state.clear()

@router.message(BotStates.waiting_for_edit_prompt, F.text)
async def handle_edit_prompt(message: Message, state: FSMContext):
    status = await message.reply("🪄 Колдую над вашей картинкой... Ждите.")
    try:
        data = await state.get_data()
        photo_bytes = data['photo_data']
        
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(
            models_list=CASCADES[mode]['edit'], 
            contents=message.text, 
            is_image=True, 
            image_bytes=photo_bytes,
            edit_mode=True
        )
        if await handle_response(message, resp, is_image=True):
            await state.clear()
            await message.answer("✨ Фото успешно изменено!", reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await message.reply(f"❌ Не удалось изменить фото: `{e}`", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    finally:
        await status.delete()

# --- DEFAULT HANDLERS ---
@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext):
    await state.clear()
    status = await message.reply("👀 Смотрю на фото...")
    try:
        photo = message.photo[-1]
        media_stream = await download_media(photo.file_id)
        
        prompt = message.caption or "Детально опиши, что изображено на этом изображении."
        contents = [
            prompt,
            types.Part.from_bytes(data=media_stream.read(), mime_type="image/jpeg")
        ]
        
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['text'], contents=contents)
        await handle_response(message, resp)
    except Exception as e:
        await message.reply(f"❌ Ой, что-то пошло не так: `{e}`")
    finally:
        await status.delete()

@router.message(F.voice | F.audio | F.video | F.document)
async def handle_media(message: Message, state: FSMContext):
    await state.clear()
    
    if message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
        status = await message.reply("👀 Смотрю на файл с изображением...")
        try:
            media_stream = await download_media(message.document.file_id)
            prompt = message.caption or "Детально опиши, что изображено на этом изображении."
            contents = [
                prompt,
                types.Part.from_bytes(data=media_stream.read(), mime_type=message.document.mime_type)
            ]
            mode = USER_MODES.get(message.from_user.id, 'flash')
            resp = await generate_with_fallback(CASCADES[mode]['text'], contents=contents)
            await handle_response(message, resp)
        except Exception as e:
            await message.reply(f"❌ Ой, что-то пошло не так: `{e}`")
        finally:
            await status.delete()
        return

    if message.document or message.video:
        await message.reply("📂 Я пока могу работать только с Фотографиями, Аудио и Голосовыми сообщениями. Документы (кроме картинок) и видео временно не поддерживаются.")
        return

    status = await message.reply("🎧 Транскрибирую ваше аудио, подождите немного...")
    try:
        audio_file = message.voice or message.audio
        media_stream = await download_media(audio_file.file_id)
        
        mime_type = audio_file.mime_type or "audio/ogg"
        
        prompt = message.caption or "Сделай транскрибацию этого аудио и кратко резюмируй о чем там говорится."
        contents = [
            prompt,
            types.Part.from_bytes(data=media_stream.read(), mime_type=mime_type)
        ]
        
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['text'], contents=contents)
        await handle_response(message, resp)
    except Exception as e:
        await message.reply(f"❌ Ошибка распознавания аудио: `{e}`")
    finally:
        await status.delete()

@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    await state.clear()
    status = await message.reply("🧠 Думаю...")
    try:
        mode = USER_MODES.get(message.from_user.id, 'flash')
        resp = await generate_with_fallback(CASCADES[mode]['text'], contents=message.text)
        await handle_response(message, resp)
    except Exception as e:
        await message.reply(f"❌ Ошибка сервиса: `{e}`")
    finally:
        await status.delete()

# --- WEBHOOK & HEALTH CHECK ---
async def handle_index(request):
    return web.Response(text="Bot is operational", status=200)

async def on_startup(bot: Bot):
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    app.router.add_get("/", handle_index)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    logging.info(f"Starting webhook server on port {PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()