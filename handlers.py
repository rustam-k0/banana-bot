"""
handlers.py — Обработчики сообщений Telegram.
"""

import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message
from google.genai import types

from config import CASCADES
from gemini import (
    call_text, call_image,
    extract_text, extract_image_bytes,
    format_error, safe_send_text,
)
from keyboards import (
    main_kb, CANCEL_KB,
    PRO_BTN, FLASH_BTN,
    BTN_DRAW, BTN_EDIT, BTN_HELP, BTN_CANCEL, BTN_MENU,
    get_mode, set_mode,
)

log = logging.getLogger("banana-bot")
router = Router()


# ── FSM ──────────────────────────────────────────────────

class S(StatesGroup):
    gen_prompt  = State()   # ждём описание картинки
    edit_photo  = State()   # ждём фото для редактирования
    edit_prompt = State()   # ждём что изменить на фото


# ── Утилиты ──────────────────────────────────────────────
# Вспомогательные функции, чтобы скачивать файлы от Telegram 
# и сбрасывать состояние (возвращать в главное меню).

async def dl(bot: Bot, file_id: str) -> io.BytesIO:
    f = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(f.file_path, buf)
    buf.seek(0)
    return buf


async def home(msg: Message, st: FSMContext, text: str = "👌 Чем помочь?"):
    """Сброс FSM → главное меню."""
    await st.clear()
    await msg.answer(text, reply_markup=main_kb(msg.from_user.id), parse_mode=None)


def _is_cancel(text: str | None) -> bool:
    return text in (BTN_CANCEL, BTN_MENU)


# ── /start ───────────────────────────────────────────────
# Стартовое приветствие. Вызывается при запуске бота.

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "🍌 Привет! Я Banana Bot\n\nЯ пишу код, рассказываю про картинки и рисую новые. Жми кнопки 👇",
        reply_markup=main_kb(msg.from_user.id),
        parse_mode="HTML",
    )


# ── /help + кнопка ❓ ────────────────────────────────────
# Показывает подробную инструкцию по использованию бота.

@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "Используй кнопки внизу для рисования и изменения картинок.\nОтправь текст или голосовое для общения.\nОтправь фото для описания.",
        reply_markup=main_kb(msg.from_user.id),
        parse_mode="HTML",
    )


# ── Отмена / Меню ───────────────────────────────────────
# Прерывает любые текущие действия пользователя (например,
# ожидание отправки картинки для генерации или редактирования).

@router.message(F.text.in_([BTN_CANCEL, BTN_MENU]))
async def btn_cancel(msg: Message, state: FSMContext):
    await home(msg, state)


# ── Переключение режимов ─────────────────────────────────
# Переключает используемые нейросети (PRO ↔ FLASH) 
# для конкретного пользователя.

@router.message(F.text.in_([PRO_BTN, FLASH_BTN]))
async def toggle_mode(msg: Message, state: FSMContext):
    await state.clear()
    cur = get_mode(msg.from_user.id)
    new = "pro" if cur == "flash" else "flash"
    set_mode(msg.from_user.id, new)
    label = "⚡ PRO" if new == "pro" else "🟢 FLASH"
    await msg.answer(f"✅ Установлен режим <b>{label}</b>", reply_markup=main_kb(msg.from_user.id), parse_mode="HTML")


# ── 🎨 Генерация картинок ────────────────────────────────
# Обработка сценария генерации: юзер жмет кнопку, бот ждет промпт (текст).

@router.message(F.text == BTN_DRAW)
async def btn_gen(msg: Message, state: FSMContext):
    await state.set_state(S.gen_prompt)
    await msg.answer(
        "🎨 Что надо нарисовать?",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@router.message(S.gen_prompt, F.text)
async def gen_prompt(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        return await home(msg, state)

    status = await msg.reply("🎨 ⏳", parse_mode="HTML")
    try:
        mode = get_mode(msg.from_user.id)
        resp = await call_image(CASCADES[mode]["image"], msg.text)
        img = extract_image_bytes(resp)
        if img:
            await msg.reply_photo(photo=BufferedInputFile(img, filename="img.png"))
            await home(msg, state, "🎉 Готово!")
        else:
            await home(msg, state, "😕 Не вышло, попробуй другое описание")
    except Exception as e:
        log.error(f"gen: {e}")
        await msg.reply(format_error("генерация", e), parse_mode="HTML")
        await home(msg, state)
    finally:
        try: await status.delete()
        except Exception: pass


# ── ✏️ Редактирование фото ───────────────────────────────
# Сценарий редактирования: юзер жмет кнопку -> бот ждет фото -> бот ждет текст.

@router.message(F.text == BTN_EDIT)
async def btn_edit(msg: Message, state: FSMContext):
    await state.set_state(S.edit_photo)
    await msg.answer("📸 Жду фото", reply_markup=CANCEL_KB, parse_mode="HTML")


@router.message(S.edit_photo, F.photo)
async def edit_photo(msg: Message, state: FSMContext, bot: Bot):
    try:
        buf = await dl(bot, msg.photo[-1].file_id)
        await state.update_data(photo=buf.read())
        await state.set_state(S.edit_prompt)
        await msg.answer(
            "👍 Теперь напиши, что надо изменить",
            reply_markup=CANCEL_KB,
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"dl: {e}")
        await msg.reply(format_error("скачивание", e), parse_mode="HTML")
        await home(msg, state)


@router.message(S.edit_photo, ~F.photo)
async def edit_not_photo(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        return await home(msg, state)
    await msg.answer("📸 Пришли картинку, а не текст", reply_markup=CANCEL_KB, parse_mode="HTML")


@router.message(S.edit_prompt, F.text)
async def edit_prompt(msg: Message, state: FSMContext):
    if _is_cancel(msg.text):
        return await home(msg, state)

    status = await msg.reply("✏️ ⏳", parse_mode="HTML")
    try:
        data = await state.get_data()
        photo = data.get("photo")
        if not photo:
            return await home(msg, state, "😕 Фото потерялось, начни заново")

        contents = [types.Part.from_bytes(data=photo, mime_type="image/jpeg"), msg.text]
        mode = get_mode(msg.from_user.id)
        resp = await call_image(CASCADES[mode]["image"], contents)
        img = extract_image_bytes(resp)
        if img:
            await msg.reply_photo(photo=BufferedInputFile(img, filename="edit.png"))
            await home(msg, state, "🎉 Готово!")
        else:
            await home(msg, state, "😕 Не вышло, попробуй другое описание")
    except Exception as e:
        log.error(f"edit: {e}")
        await msg.reply(format_error("редактирование", e), parse_mode="HTML")
        await home(msg, state)
    finally:
        try: await status.delete()
        except Exception: pass


# ── 📸 Фото (анализ) ────────────────────────────────────
# Если пользователь просто прислал фото (не в режиме редактирования),
# бот автоматически анализирует его с помощью Gemini Vision.

@router.message(F.photo)
async def handle_photo(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    status = await msg.reply("🔍 ⏳", parse_mode="HTML")
    try:
        buf = await dl(bot, msg.photo[-1].file_id)
        prompt = msg.caption or "Подробно опиши, что изображено на этом фото."
        contents = [prompt, types.Part.from_bytes(data=buf.read(), mime_type="image/jpeg")]
        resp = await call_text(CASCADES[get_mode(msg.from_user.id)]["text"], contents)
        text = extract_text(resp)
        if text:
            await safe_send_text(msg, text)
        else:
            await msg.reply("😕 Не разобрал фото", parse_mode=None)
    except Exception as e:
        log.error(f"photo: {e}")
        await msg.reply(format_error("фото", e), parse_mode="HTML")
    finally:
        try: await status.delete()
        except Exception: pass


# ── 🎙 Голосовые ─────────────────────────────────────────
# Отправляем голосовые записи и аудиофайлы напрямую в модель,
# прося ее расшифровать и ответить.

@router.message(F.voice | F.audio)
async def handle_voice(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    status = await msg.reply("🎧 ⏳", parse_mode="HTML")
    try:
        a = msg.voice or msg.audio
        buf = await dl(bot, a.file_id)
        mime = a.mime_type or "audio/ogg"
        prompt = "Расшифруй голосовое и ответь на вопрос, если он есть."
        contents = [prompt, types.Part.from_bytes(data=buf.read(), mime_type=mime)]
        resp = await call_text(CASCADES[get_mode(msg.from_user.id)]["text"], contents)
        text = extract_text(resp)
        if text:
            await safe_send_text(msg, text)
        else:
            await msg.reply("😕 Не разобрал", parse_mode=None)
    except Exception as e:
        log.error(f"voice: {e}")
        await msg.reply(format_error("голос", e), parse_mode="HTML")
    finally:
        try: await status.delete()
        except Exception: pass


# ── 📂 Документы ─────────────────────────────────────────
# Если прислали как документ, но тип image/* — анализируем как фото.
# Остальные форматы пока отклоняем.

@router.message(F.document)
async def handle_doc(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    mime = msg.document.mime_type or ""
    if mime.startswith("image/"):
        status = await msg.reply("🔍 ⏳", parse_mode="HTML")
        try:
            buf = await dl(bot, msg.document.file_id)
            prompt = msg.caption or "Подробно опиши, что изображено на этом фото."
            contents = [prompt, types.Part.from_bytes(data=buf.read(), mime_type=mime)]
            resp = await call_text(CASCADES[get_mode(msg.from_user.id)]["text"], contents)
            text = extract_text(resp)
            if text:
                await safe_send_text(msg, text)
            else:
                await msg.reply("😕 Не разобрал", parse_mode=None)
        except Exception as e:
            log.error(f"doc: {e}")
            await msg.reply(format_error("анализ", e), parse_mode=None)
        finally:
            try: await status.delete()
            except Exception: pass
    else:
        await msg.reply("Только фото/аудио/документ-картинки.", parse_mode="HTML")


# ── 🎬 Видео ────────────────────────────────────────────
# Временная заглушка для видео-файлов.

@router.message(F.video | F.video_note)
async def handle_video(msg: Message, state: FSMContext):
    await state.clear()
    await msg.reply("Видео пока не поддерживается.", parse_mode="HTML")


# ── 💬 Текст (catch-all, ПОСЛЕДНИМ!) ────────────────────
# Сюда падают все сообщения, которые не подошли под верхние фильтры.
# Не сохраняет историю, отправляет только текст текущего сообщения.

@router.message(F.text)
async def handle_text(msg: Message, state: FSMContext):
    await state.clear()
    status = await msg.reply("💭", parse_mode="HTML")
    try:
        resp = await call_text(CASCADES[get_mode(msg.from_user.id)]["text"], msg.text)
        text = extract_text(resp)
        if text:
            await safe_send_text(msg, text)
        else:
            await msg.reply("😕 Пустой ответ, спроси иначе", parse_mode=None)
    except Exception as e:
        log.error(f"text: {e}")
        await msg.reply(format_error("текст", e), parse_mode="HTML")
    finally:
        try: await status.delete()
        except Exception: pass