"""
Banana Bot — Telegram-бот для доступа к Google Gemini API.
Поддерживает: текст, голосовые сообщения, анализ фото,
генерацию и редактирование изображений.
Два режима: PRO (лучшее качество) и FLASH (экономичный).
"""

import asyncio
import io
import logging
import os
import sys
import textwrap
import base64

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

# ═══════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")          # Если пусто — polling
PORT = int(os.getenv("PORT", 8080))

if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    sys.exit("❌ КРИТИЧЕСКАЯ ОШИБКА: не заданы TELEGRAM_BOT_TOKEN или GOOGLE_API_KEY в .env")

# Белый список пользователей (через запятую в .env)
ALLOWED_USERS: set[int] = set()
for uid in os.getenv("ALLOWED_USERS", "").split(","):
    uid = uid.strip()
    if uid.isdigit():
        ALLOWED_USERS.add(int(uid))

if not ALLOWED_USERS:
    logging.warning("⚠️ ALLOWED_USERS пуст — бот не пустит никого!")

# ═══════════════════════════════════════════════════════════
# МОДЕЛИ И КАСКАДЫ
# ═══════════════════════════════════════════════════════════
#
# PRO  — лучшее качество, дороже
# FLASH — быстро и экономично
#
# Каскад: если первая модель вернёт ошибку (429/500/503),
# бот переключится на следующую.
# ═══════════════════════════════════════════════════════════

CASCADES = {
    "pro": {
        "text": ["gemini-3.1-pro", "gemini-2.5-flash"],
        "image": ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"],
    },
    "flash": {
        "text": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
        "image": ["gemini-2.5-flash-image"],
    },
}

# Отключаем фильтры безопасности (по максимуму)
SAFETY_OFF = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
]

# Хранение выбранного режима для каждого пользователя
USER_MODES: dict[int, str] = {}

# ═══════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("banana-bot")

client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()


# ═══════════════════════════════════════════════════════════
# MIDDLEWARE — ПРОВЕРКА ДОСТУПА
# ═══════════════════════════════════════════════════════════

class AuthMiddleware(BaseMiddleware):
    """Пропускает только пользователей из ALLOWED_USERS."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user:
            return  # нет пользователя — игнорируем

        if user.id not in ALLOWED_USERS:
            log.warning(f"⛔ Отказ в доступе: user_id={user.id}, username=@{user.username}")
            # Пытаемся ответить пользователю
            msg = getattr(event, "message", None) or (event if isinstance(event, Message) else None)
            if msg and hasattr(msg, "reply"):
                try:
                    await msg.reply(
                        f"⛔️ *Доступ запрещён*\n\n"
                        f"Этот бот является приватным.\n"
                        f"Ваш Telegram ID: `{user.id}`\n\n"
                        f"Передайте этот ID администратору бота,\n"
                        f"чтобы вас добавили в список доступа."
                    )
                except Exception:
                    pass
            return

        return await handler(event, data)


dp.message.middleware(AuthMiddleware())
dp.include_router(router)


# ═══════════════════════════════════════════════════════════
# СОСТОЯНИЯ (FSM)
# ═══════════════════════════════════════════════════════════

class BotStates(StatesGroup):
    waiting_for_gen_prompt = State()      # Ждём текст для генерации картинки
    waiting_for_edit_photo = State()      # Ждём фото для редактирования
    waiting_for_edit_prompt = State()     # Ждём промпт для редактирования фото


# ═══════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════

def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Главная клавиатура с учётом текущего режима пользователя."""
    mode = USER_MODES.get(user_id, "flash")
    if mode == "pro":
        mode_btn = "💎 Режим: PRO (Лучшее качество)"
    else:
        mode_btn = "🚀 Режим: FLASH (Быстрый)"

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎨 Сгенерировать картинку"),
             KeyboardButton(text="🪄 Изменить фото")],
            [KeyboardButton(text=mode_btn)],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
    )


CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)


# ═══════════════════════════════════════════════════════════
# ЛОГИКА РАБОТЫ С GEMINI API
# ═══════════════════════════════════════════════════════════

async def call_gemini_text(models: list[str], contents) -> object:
    """
    Отправляет текстовый / мультимодальный запрос в Gemini.
    Перебирает модели из каскада при ошибках.
    """
    last_error = None
    for model_name in models:
        try:
            log.info(f"📤 Запрос к модели: {model_name}")
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(safety_settings=SAFETY_OFF),
            )
            return response

        except APIError as e:
            last_error = e
            log.warning(f"⚠️ Ошибка API [{model_name}]: {e}")
            # При перегрузке — пробуем следующую модель
            if any(code in str(e) for code in ["429", "500", "503"]):
                continue
            break  # Остальные ошибки — не фоллбэчим
        except Exception as e:
            last_error = e
            log.error(f"❌ Неожиданная ошибка [{model_name}]: {e}")
            break

    raise last_error or Exception("Все модели недоступны.")


async def call_gemini_image(models: list[str], contents) -> object:
    """
    Отправляет запрос на генерацию/редактирование изображения.
    Использует generate_content с response_modalities=["IMAGE"].
    """
    last_error = None
    for model_name in models:
        try:
            log.info(f"🎨 Запрос изображения к модели: {model_name}")
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    safety_settings=SAFETY_OFF,
                ),
            )
            return response

        except APIError as e:
            last_error = e
            log.warning(f"⚠️ Ошибка API [{model_name}]: {e}")
            if any(code in str(e) for code in ["429", "500", "503"]):
                continue
            break
        except Exception as e:
            last_error = e
            log.error(f"❌ Неожиданная ошибка [{model_name}]: {e}")
            break

    raise last_error or Exception("Все модели для генерации изображений недоступны.")


def extract_text(response) -> str | None:
    """Извлекает текст из ответа Gemini."""
    try:
        if hasattr(response, "text") and response.text:
            return response.text
    except Exception:
        pass

    try:
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
    except Exception:
        pass

    return None


def extract_image_bytes(response) -> bytes | None:
    """Извлекает байты изображения из ответа Gemini."""
    try:
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    return part.inline_data.data
    except Exception:
        pass
    return None


async def send_text_response(message: Message, text: str):
    """
    Отправляет текстовый ответ пользователю.
    Если текст длинный — разбивает на куски по 4000 символов.
    Если Markdown ломает форматирование — отправляет без разметки.
    """
    chunks = textwrap.wrap(text, width=4000, break_long_words=False, replace_whitespace=False)
    if not chunks:
        chunks = [text[:4000]]

    for chunk in chunks:
        try:
            await message.answer(chunk)
        except Exception:
            # Markdown мог сломаться — пробуем без него
            try:
                await message.answer(chunk, parse_mode=None)
            except Exception as e:
                log.error(f"Не удалось отправить текст: {e}")


async def download_telegram_file(file_id: str) -> io.BytesIO:
    """Скачивает файл из Telegram в память."""
    tg_file = await bot.get_file(file_id)
    buffer = io.BytesIO()
    await bot.download_file(tg_file.file_path, buffer)
    buffer.seek(0)
    return buffer


def get_user_mode(user_id: int) -> str:
    """Возвращает текущий режим пользователя (flash по умолчанию)."""
    return USER_MODES.get(user_id, "flash")


def error_message(context: str, error: Exception) -> str:
    """Формирует понятное сообщение об ошибке для пользователя."""
    err_str = str(error)
    if len(err_str) > 300:
        err_str = err_str[:300] + "…"
    return (
        f"❌ *Ошибка: {context}*\n\n"
        f"`{err_str}`\n\n"
        f"Попробуйте ещё раз или переключите режим."
    )


# ═══════════════════════════════════════════════════════════
# ОБРАБОТЧИКИ КОМАНД
# ═══════════════════════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Приветствие и главная клавиатура."""
    await state.clear()
    USER_MODES.setdefault(message.from_user.id, "flash")
    await message.answer(
        "👋 *Добро пожаловать в Banana Bot!*\n\n"
        "Я — ваш ИИ-ассистент на базе Google Gemini.\n\n"
        "🔹 Отправьте *текст* — я отвечу на любой вопрос\n"
        "🔹 Отправьте *голосовое* — я расшифрую и отвечу\n"
        "🔹 Отправьте *фото* — я расскажу, что на нём\n"
        "🔹 Нажмите *«Сгенерировать картинку»* — я нарисую\n"
        "🔹 Нажмите *«Изменить фото»* — я переделаю ваше фото\n\n"
        "Используйте кнопки внизу 👇",
        reply_markup=main_keyboard(message.from_user.id),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    """Подробная справка."""
    await state.clear()
    await message.answer(
        "💡 *Как пользоваться ботом:*\n\n"
        "✉️ *Текст* — просто напишите любой вопрос,\n"
        "и я на него отвечу.\n\n"
        "🎤 *Голосовое сообщение* — отправьте голосовое,\n"
        "я расшифрую его и отвечу на вопрос внутри.\n\n"
        "📷 *Фотография* — отправьте мне любое фото.\n"
        "Я расскажу что на нём. Можете добавить подпись,\n"
        "например: _«переведи текст на этом фото»_\n\n"
        "🎨 *Создать картинку* — нажмите кнопку\n"
        "и опишите, что хотите увидеть.\n\n"
        "🪄 *Изменить фото* — нажмите кнопку,\n"
        "отправьте фото и напишите, что изменить.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "*Режимы работы:*\n\n"
        "🚀 *FLASH* — быстрый и экономичный,\n"
        "подходит для повседневных задач.\n\n"
        "💎 *PRO* — самая мощная модель,\n"
        "для сложных расчётов и глубокого анализа.\n\n"
        "Переключайте режим кнопкой в меню.",
        reply_markup=main_keyboard(message.from_user.id),
    )


# ═══════════════════════════════════════════════════════════
# КНОПКА ОТМЕНЫ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "❌ Отмена")
async def btn_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "↩️ Действие отменено. Жду ваших команд!",
        reply_markup=main_keyboard(message.from_user.id),
    )


# ═══════════════════════════════════════════════════════════
# ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ
# ═══════════════════════════════════════════════════════════

@router.message(F.text.in_(["💎 Режим: PRO (Лучшее качество)", "🚀 Режим: FLASH (Быстрый)"]))
async def toggle_mode(message: Message, state: FSMContext):
    await state.clear()
    current = get_user_mode(message.from_user.id)
    new_mode = "pro" if current == "flash" else "flash"
    USER_MODES[message.from_user.id] = new_mode

    if new_mode == "pro":
        label = "💎 PRO — максимальное качество"
    else:
        label = "🚀 FLASH — быстрый и экономичный"

    await message.answer(
        f"✅ Режим переключён на *{label}*",
        reply_markup=main_keyboard(message.from_user.id),
    )


# ═══════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "🎨 Сгенерировать картинку")
async def btn_generate_image(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_gen_prompt)
    await message.answer(
        "📝 Опишите картинку, которую хотите получить.\n\n"
        "Например:\n"
        "_«Рыжий кот в космическом скафандре на Луне»_\n\n"
        "Чем подробнее описание — тем лучше результат!",
        reply_markup=CANCEL_KB,
        parse_mode="Markdown",
    )


@router.message(BotStates.waiting_for_gen_prompt, F.text)
async def handle_gen_prompt(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        return await btn_cancel(message, state)

    status_msg = await message.reply("🎨 Рисую изображение… Подождите немного.")
    try:
        mode = get_user_mode(message.from_user.id)
        response = await call_gemini_image(
            models=CASCADES[mode]["image"],
            contents=message.text,
        )

        img_bytes = extract_image_bytes(response)
        if img_bytes:
            await message.reply_photo(
                photo=BufferedInputFile(img_bytes, filename="generated.jpg"),
            )
            await state.clear()
            await message.answer(
                "✨ Готово! Что-нибудь ещё?",
                reply_markup=main_keyboard(message.from_user.id),
            )
        else:
            await message.reply(
                "⚠️ Не удалось создать изображение.\n"
                "Возможно, запрос был отклонён фильтрами безопасности.\n"
                "Попробуйте изменить описание.",
                reply_markup=main_keyboard(message.from_user.id),
            )
            await state.clear()

    except Exception as e:
        log.error(f"Ошибка генерации изображения: {e}")
        await message.reply(
            error_message("генерация изображения", e),
            reply_markup=main_keyboard(message.from_user.id),
        )
        await state.clear()
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# РЕДАКТИРОВАНИЕ ИЗОБРАЖЕНИЙ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "🪄 Изменить фото")
async def btn_edit_image(message: Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_edit_photo)
    await message.answer(
        "🖼 Отправьте фото, которое хотите изменить.",
        reply_markup=CANCEL_KB,
    )


@router.message(BotStates.waiting_for_edit_photo, F.photo)
async def handle_edit_photo(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1]  # Лучшее качество
        buffer = await download_telegram_file(photo.file_id)
        await state.update_data(photo_bytes=buffer.read())
        await state.set_state(BotStates.waiting_for_edit_prompt)
        await message.answer(
            "📝 Отлично! Теперь опишите, что хотите изменить.\n\n"
            "Например:\n"
            "_«Добавь солнечные очки на лицо»_\n"
            "_«Сделай фон зимним пейзажем»_",
            reply_markup=CANCEL_KB,
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error(f"Ошибка загрузки фото: {e}")
        await message.reply(
            error_message("загрузка фото", e),
            reply_markup=main_keyboard(message.from_user.id),
        )
        await state.clear()


@router.message(BotStates.waiting_for_edit_photo, ~F.photo)
async def handle_edit_not_photo(message: Message, state: FSMContext):
    """Пользователь отправил не фото в режиме ожидания фото."""
    if message.text == "❌ Отмена":
        return await btn_cancel(message, state)
    await message.answer(
        "⚠️ Пожалуйста, отправьте именно *фотографию*,\n"
        "а не текст или файл.\n\n"
        "Или нажмите «❌ Отмена» для возврата в меню.",
        reply_markup=CANCEL_KB,
    )


@router.message(BotStates.waiting_for_edit_prompt, F.text)
async def handle_edit_prompt(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        return await btn_cancel(message, state)

    status_msg = await message.reply("🪄 Обрабатываю ваше фото… Подождите.")
    try:
        data = await state.get_data()
        photo_bytes = data.get("photo_bytes")
        if not photo_bytes:
            await message.reply(
                "⚠️ Фото потерялось. Начните сначала: нажмите «🪄 Изменить фото».",
                reply_markup=main_keyboard(message.from_user.id),
            )
            await state.clear()
            return

        # Формируем мультимодальный запрос: фото + промпт
        contents = [
            types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"),
            message.text,
        ]

        mode = get_user_mode(message.from_user.id)
        response = await call_gemini_image(
            models=CASCADES[mode]["image"],
            contents=contents,
        )

        img_bytes = extract_image_bytes(response)
        if img_bytes:
            await message.reply_photo(
                photo=BufferedInputFile(img_bytes, filename="edited.jpg"),
            )
            await state.clear()
            await message.answer(
                "✨ Фото изменено! Что-нибудь ещё?",
                reply_markup=main_keyboard(message.from_user.id),
            )
        else:
            await message.reply(
                "⚠️ Не удалось изменить изображение.\n"
                "Фильтры безопасности могли отклонить запрос.\n"
                "Попробуйте другое описание.",
                reply_markup=main_keyboard(message.from_user.id),
            )
            await state.clear()

    except Exception as e:
        log.error(f"Ошибка редактирования фото: {e}")
        await message.reply(
            error_message("редактирование фото", e),
            reply_markup=main_keyboard(message.from_user.id),
        )
        await state.clear()
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# ОБРАБОТКА ФОТОГРАФИЙ (вне режима редактирования)
# ═══════════════════════════════════════════════════════════

@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext):
    """Пользователь отправил фото — анализируем его через Gemini."""
    await state.clear()
    status_msg = await message.reply("👀 Анализирую фото…")
    try:
        photo = message.photo[-1]
        buffer = await download_telegram_file(photo.file_id)

        prompt = message.caption or "Подробно опиши, что изображено на этом фото."
        contents = [
            prompt,
            types.Part.from_bytes(data=buffer.read(), mime_type="image/jpeg"),
        ]

        mode = get_user_mode(message.from_user.id)
        response = await call_gemini_text(CASCADES[mode]["text"], contents)

        text = extract_text(response)
        if text:
            await send_text_response(message, text)
        else:
            await message.reply(
                "⚠️ Не удалось проанализировать фото.\n"
                "Попробуйте другое изображение."
            )
    except Exception as e:
        log.error(f"Ошибка анализа фото: {e}")
        await message.reply(error_message("анализ фото", e))
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# ГОЛОСОВЫЕ И АУДИО СООБЩЕНИЯ
# ═══════════════════════════════════════════════════════════

@router.message(F.voice | F.audio)
async def handle_voice(message: Message, state: FSMContext):
    """Голосовое сообщение — транскрипция + ответ."""
    await state.clear()
    status_msg = await message.reply("🎧 Слушаю ваше сообщение…")
    try:
        audio = message.voice or message.audio
        buffer = await download_telegram_file(audio.file_id)
        mime = audio.mime_type or "audio/ogg"

        prompt = (
            "Прослушай это аудиосообщение. "
            "Сначала запиши текст дословно, затем ответь на вопрос, "
            "если в сообщении есть вопрос. Если вопроса нет — "
            "кратко резюмируй о чём говорится."
        )
        contents = [
            prompt,
            types.Part.from_bytes(data=buffer.read(), mime_type=mime),
        ]

        mode = get_user_mode(message.from_user.id)
        response = await call_gemini_text(CASCADES[mode]["text"], contents)

        text = extract_text(response)
        if text:
            await send_text_response(message, text)
        else:
            await message.reply("⚠️ Не удалось распознать голосовое сообщение.")
    except Exception as e:
        log.error(f"Ошибка распознавания аудио: {e}")
        await message.reply(error_message("распознавание аудио", e))
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# ДОКУМЕНТЫ (ИЗОБРАЖЕНИЯ КАК ФАЙЛЫ)
# ═══════════════════════════════════════════════════════════

@router.message(F.document)
async def handle_document(message: Message, state: FSMContext):
    """Обработка документов — только изображения."""
    await state.clear()

    # Проверяем, является ли документ изображением
    if message.document.mime_type and message.document.mime_type.startswith("image/"):
        status_msg = await message.reply("👀 Анализирую изображение…")
        try:
            buffer = await download_telegram_file(message.document.file_id)
            prompt = message.caption or "Подробно опиши, что изображено на этом фото."
            contents = [
                prompt,
                types.Part.from_bytes(
                    data=buffer.read(),
                    mime_type=message.document.mime_type,
                ),
            ]
            mode = get_user_mode(message.from_user.id)
            response = await call_gemini_text(CASCADES[mode]["text"], contents)

            text = extract_text(response)
            if text:
                await send_text_response(message, text)
            else:
                await message.reply("⚠️ Не удалось проанализировать изображение.")
        except Exception as e:
            log.error(f"Ошибка анализа документа-изображения: {e}")
            await message.reply(error_message("анализ изображения", e))
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass
    else:
        await message.reply(
            "📂 Извините, я пока работаю только с:\n"
            "• текстом\n"
            "• фотографиями\n"
            "• голосовыми сообщениями\n\n"
            "Документы и видео пока не поддерживаются."
        )


# ═══════════════════════════════════════════════════════════
# ВИДЕО — ЗАГЛУШКА
# ═══════════════════════════════════════════════════════════

@router.message(F.video | F.video_note)
async def handle_video(message: Message, state: FSMContext):
    await state.clear()
    await message.reply(
        "🎬 Извините, видео пока не поддерживается.\n"
        "Отправьте текст, фото или голосовое сообщение."
    )


# ═══════════════════════════════════════════════════════════
# ТЕКСТОВЫЕ СООБЩЕНИЯ (обычный чат)
# ═══════════════════════════════════════════════════════════

@router.message(F.text)
async def handle_text(message: Message, state: FSMContext):
    """Обычный текстовый запрос → Gemini."""
    await state.clear()
    status_msg = await message.reply("🧠 Думаю…")
    try:
        mode = get_user_mode(message.from_user.id)
        response = await call_gemini_text(CASCADES[mode]["text"], message.text)

        text = extract_text(response)
        if text:
            await send_text_response(message, text)
        else:
            await message.reply(
                "⚠️ Пустой ответ от нейросети.\n"
                "Попробуйте переформулировать вопрос."
            )
    except Exception as e:
        log.error(f"Ошибка текстового запроса: {e}")
        await message.reply(error_message("обработка запроса", e))
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# ЗАПУСК БОТА
# ═══════════════════════════════════════════════════════════

async def on_startup(bot_instance: Bot, **kwargs):
    """Устанавливает webhook при старте (вызывается из dp.startup)."""
    await bot_instance.set_webhook(
        f"{WEBHOOK_URL}/webhook",
        drop_pending_updates=True,
    )
    log.info(f"✅ Webhook установлен: {WEBHOOK_URL}/webhook")


def main():
    """
    Точка входа:
    - Если задан WEBHOOK_URL → запуск через webhook + aiohttp (Render)
    - Если WEBHOOK_URL пуст → запуск через polling (локальная разработка)
    """
    if WEBHOOK_URL:
        # ── WEBHOOK-РЕЖИМ (Render, Railway, Heroku) ──
        from aiohttp import web
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Banana Bot is running 🍌"))
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        setup_application(app, dp, bot=bot)  # это передаёт bot в on_startup
        dp.startup.register(on_startup)

        log.info(f"🚀 Запуск в WEBHOOK-режиме на порту {PORT}")
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        # ── POLLING-РЕЖИМ ──
        log.info("🚀 Запуск в POLLING-режиме (WEBHOOK_URL не задан)")
        asyncio.run(start_polling())


async def start_polling():
    """Запуск бота в polling-режиме."""
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("✅ Бот запущен! Ожидаю сообщений…")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    main()