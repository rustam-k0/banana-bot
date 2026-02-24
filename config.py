"""
config.py — Конфигурация бота Banana Bot.

Здесь загружаются все переменные окружения из файла .env
и определяются модели для каждого режима работы бота.
"""

import logging
import os
import sys

from dotenv import load_dotenv
from google.genai import types

# Загружаем переменные из .env файла в текущей папке
load_dotenv()


# ─────────────────────────────────────────────────────────
# ОБЯЗАТЕЛЬНЫЕ ПЕРЕМЕННЫЕ
# Без этих двух ключей бот не сможет работать.
# ─────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    sys.exit(
        "❌ КРИТИЧЕСКАЯ ОШИБКА: не заданы TELEGRAM_BOT_TOKEN "
        "или GOOGLE_API_KEY в файле .env — бот не может запуститься."
    )


# ─────────────────────────────────────────────────────────
# ОПЦИОНАЛЬНЫЕ ПЕРЕМЕННЫЕ
# WEBHOOK_URL нужен только для серверного деплоя (Render и т.д.)
# Если его нет — бот работает в polling-режиме (для разработки).
# ─────────────────────────────────────────────────────────

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", 8080))


# ─────────────────────────────────────────────────────────
# БЕЛЫЙ СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# Только эти Telegram User ID смогут написать боту.
# Указываются через запятую в .env, например: 123456,789012
# ─────────────────────────────────────────────────────────

ALLOWED_USERS: set[int] = set()
for _uid in os.getenv("ALLOWED_USERS", "").split(","):
    _uid = _uid.strip()
    if _uid.isdigit():
        ALLOWED_USERS.add(int(_uid))

if not ALLOWED_USERS:
    logging.warning(
        "⚠️ Переменная ALLOWED_USERS пуста — бот не пропустит ни одного пользователя! "
        "Укажите хотя бы один Telegram ID в .env файле."
    )


# ─────────────────────────────────────────────────────────
# КАСКАДЫ МОДЕЛЕЙ
#
# Для каждого режима (PRO / FLASH) задан список моделей.
# Если первая модель отвечает ошибкой (перегрузка, лимит),
# бот автоматически пробует следующую модель из списка.
#
# МОДЕЛИ (актуальные на февраль 2026):
#
# Текст:
#   gemini-3.1-pro         — новейшая флагманская модель (preview, самая мощная)
#   gemini-3-flash-preview — мощная и быстрая модель нового поколения (preview)
#   gemini-2.5-pro         — стабильная флагманская модель (GA)
#   gemini-2.5-flash       — быстрая и дешёвая модель (GA)
#
# Картинки:
#   gemini-3-pro-image-preview — лучшая для генерации/редактирования картинок
#   gemini-2.5-flash-image     — быстрая модель для картинок (GA)
# ─────────────────────────────────────────────────────────

CASCADES = {
    # PRO — лучшее качество, для сложных задач и красивых картинок
    "pro": {
        "text":  ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-2.0-pro-exp-02-05"],
        "image": ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"],
    },
    # FLASH — быстро и продвинуто, для повседневных вопросов
    "flash": {
        "text":  ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "image": ["gemini-2.5-flash-image"],
    },
}


# ─────────────────────────────────────────────────────────
# НАСТРОЙКИ БЕЗОПАСНОСТИ
# Отключаем все фильтры Google, чтобы бот мог отвечать
# на любые вопросы без цензуры.
# ─────────────────────────────────────────────────────────

SAFETY_OFF = [
    types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",        threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
    types.SafetySetting(category="HARM_CATEGORY_HARASSMENT",         threshold="BLOCK_NONE"),
]
