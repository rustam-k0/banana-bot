"""
gemini.py — Модуль для работы с Google Gemini API.
"""

import logging
import textwrap

from google import genai
from google.genai import types
from google.genai.errors import APIError

from config import GOOGLE_API_KEY, SAFETY_OFF

log = logging.getLogger("banana-bot")
client = genai.Client(api_key=GOOGLE_API_KEY)


# ── Каскадные вызовы ─────────────────────────────────────

async def call_text(models: list[str], contents):
    """Текстовый/мультимодальный запрос с каскадом моделей."""
    last_error = None
    for name in models:
        try:
            log.info(f"📤 → {name}")
            return await client.aio.models.generate_content(
                model=name,
                contents=contents,
                config=types.GenerateContentConfig(
                    safety_settings=SAFETY_OFF,
                    # Ускорение: ограничиваем «thinking» до минимума
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                ),
            )
        except APIError as e:
            last_error = e
            log.warning(f"⚠️ {name}: {str(e)[:200]}")
            if any(c in str(e) for c in ("429", "500", "503")):
                continue
            break
        except Exception as e:
            last_error = e
            log.error(f"❌ {name}: {e}")
            break
    if last_error:
        raise last_error
    raise RuntimeError("Все модели недоступны")


async def call_image(models: list[str], contents):
    """Генерация/редактирование картинки с каскадом моделей."""
    last_error = None
    for name in models:
        try:
            log.info(f"🖼 → {name}")
            return await client.aio.models.generate_content(
                model=name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    safety_settings=SAFETY_OFF,
                ),
            )
        except APIError as e:
            last_error = e
            log.warning(f"⚠️ {name}: {str(e)[:200]}")
            if any(c in str(e) for c in ("429", "500", "503")):
                continue
            break
        except Exception as e:
            last_error = e
            log.error(f"❌ {name}: {e}")
            break
    if last_error:
        raise last_error
    raise RuntimeError("Модели для картинок недоступны")


# ── Парсинг ответов ──────────────────────────────────────

def extract_text(response) -> str | None:
    """Достаёт текст из ответа."""
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
    """Достаёт байты картинки из ответа (inline_data)."""
    try:
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                    return part.inline_data.data
    except Exception:
        pass
    return None


# ── Парсинг Markdown в HTML (для Telegram) ───────────────

import re
import html

def md_to_tg_html(text: str) -> str:
    """Конвертирует базовый Markdown от Gemini в поддерживаемый Telegram HTML."""
    # Экранируем спецсимволы, чтобы Telegram не падал от <, >
    text = html.escape(text)
    
    # Жирный: **text** -> <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Курсив: *text* -> <i>text</i>  (только если не внутри слов)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'<i>\1</i>', text)
    
    # Строчный код: `code` -> <code>code</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    # Блоки кода (убираем экранирование внутри pre)
    def repl_pre(m):
        # Деэкранируем обратно, так как Telegram `pre` это позволяет
        inner = html.unescape(m.group(1))
        # Снова экранируем критичные
        inner = inner.replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre>{inner}</pre>"
    text = re.sub(r'```(?:\w*\n)?(.*?)```', repl_pre, text, flags=re.DOTALL)
    
    return text


# ── Вспомогательные ──────────────────────────────────────

def format_error(context: str, error: Exception) -> str:
    """Формирует короткое сообщение об ошибке (с экранированием HTML)."""
    err = html.escape(str(error))
    if len(err) > 100:
        err = err[:100] + "…"
    return f"❌ Ошибка (<b>{context}</b>):\n<code>{err}</code>\n\nПопробуй сменить режим ⚡↔🟢"


async def safe_send_text(message, text: str):
    """Отправляет текст кусками, конвертируя Markdown в HTML."""
    html_text = md_to_tg_html(text)
    
    chunks = textwrap.wrap(
        html_text, width=4000,
        break_long_words=False,
        replace_whitespace=False,
    )
    if not chunks:
        chunks = [html_text[:4000]]

    for chunk in chunks:
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            log.error(f"Не удалось отправить HTML: {e}")
            # Fallback на plain text
            await message.answer(chunk[:4000], parse_mode=None)