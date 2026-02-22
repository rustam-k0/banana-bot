# Banana Telegram Bot (Gemini AI Proxy)

Быстрый, асинхронный бот-прокси для Google Gemini API.

## Запуск в одну команду

Самый надежный способ запуска бота:
```bash
venv/bin/python bot.py
```

### Первая настройка (Установка с нуля)
Если вы скачали этот репозиторий впервые:

1. Переименуйте `.env.example` в `.env` и впишите туда свои ключи и `Telegram User ID`.
2. Создайте окружение и установите библиотеки:
   ```bash
   python -m venv venv
   venv/bin/pip install -r requirements.txt
   ```
3. Запустите бота:
   ```bash
   venv/bin/python bot.py
   ```

> **Почему бот мог выдавать ошибку `No module named 'PIL'`?**  
> При вводе команды просто `python bot.py` система могла запустить бота через глобальный Python, а не через виртуальное окружение `venv`. Используя прямую команду `venv/bin/python` мы 100% запускаем бота в нужном окружении.
