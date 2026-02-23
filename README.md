# Gemini Telegram Proxy

Асинхронный Telegram-бот на Webhooks с динамической маршрутизацией запросов к Google Gemini API (Paid Tier 1).

## Архитектура моделей (Управление стейтом)
Маршрутизация переключается напрямую в интерфейсе Telegram.
* **Режим PRO (Высокая стоимость/Точность):**
  * Текст / Vision: `gemini-3.1-pro` -> `gemini-3.0-flash`
  * Генерация / Edit: `nano-banana-pro` -> `nano-banana`
* **Режим FLASH (Низкая стоимость/Скорость) - Default:**
  * Текст / Vision: `gemini-3.0-flash` -> `gemini-2.5-flash`
  * Генерация / Edit: `nano-banana` -> `gemini-2.5-flash-image`

Отказоустойчивость: при превышении лимитов (429/503) запрос пробрасывается вниз по каскаду выбранного режима.

## Стек
* Python 3.10+
* `aiogram 3.x`, `aiohttp` (Webhooks)
* `google-genai`

## Развертывание
1. Форкни репозиторий.
2. Пропиши `TELEGRAM_BOT_TOKEN`, `GOOGLE_API_KEY`, `ALLOWED_USERS` и `WEBHOOK_URL` в переменные окружения.
3. Команда сборки: `pip install -r requirements.txt`
4. Команда запуска: `python bot.py`