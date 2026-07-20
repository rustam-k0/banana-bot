# 🍌 Banana Bot

Telegram bot for multi-provider AI image workflows. It helps users generate images from text or voice prompts and edit uploaded photos directly inside Telegram.

![Banana Bot result demo](assets/telegram-demo-result.jpg)

## What It Does

- `🎨 Generate Art` creates an image from a text or voice prompt.
- `🪄 Edit Photo` edits a user-provided photo using a text or voice instruction.
- `💎 PRO / ⚡️ FLASH` switches between higher-detail and faster image modes.
- `🌐 Language / Язык` switches the interface between English and Russian.
- `💡 Help` shows a short usage guide inside Telegram.

## Interface Preview

The bot is designed around a very simple Telegram-first interface so users do not need to learn a separate AI app.

![Banana Bot menu demo](assets/telegram-demo-menu.jpg)

## Current Scope

This repository currently implements a focused media bot, not a full general-purpose chat assistant.

- Supported inputs: text, voice messages, and photos.
- Supported outputs: generated or edited images, plus transcribed voice text shown back to the user.
- Conversation memory is minimal and state-based: the bot uses FSM to keep track of the current step in the image flow.
- If `ALLOWED_USERS` is set, the bot works as a private whitelist-only bot. If it is empty, the bot becomes publicly reachable.

## Models And Modes

| Mode | Primary image generation / edit | Google fallback | Voice transcription |
| --- | --- | --- | --- |
| `PRO` | OpenAI `gpt-image-2` (`medium`, automatic aspect ratio) | `gemini-3.1-flash-image` | OpenAI `gpt-4o-mini-transcribe`, then `gemini-3.5-flash` |
| `FLASH` | xAI `grok-imagine-image-quality` (1K) | `gemini-3.1-flash-image` | OpenAI `gpt-4o-mini-transcribe`, then `gemini-3.5-flash` |

The bot switches to Google automatically when a primary provider is unavailable, rate-limited, or returns no media. Safety-filter rejections are not retried through another provider.

## Tech Stack

- [Aiogram 3.x](https://docs.aiogram.dev/) for Telegram bot routing and FSM flows
- OpenAI Image API for high-quality generation and editing
- xAI Imagine API for the fast image mode
- `aiohttp` for webhook serving
- `redis` as optional FSM storage
- `texts.py` for bilingual UI copy
- `config.py` for centralized runtime and model configuration

## Project Layout

```text
.
├── bot.py           # Telegram handlers and provider workflow logic
├── config.py        # Environment loading and model configuration
├── texts.py         # Localized user-facing strings
├── assets/          # README screenshots
└── requirements.txt
```

## Why This Structure

The codebase is still compact, but it now has a small separation between:

- runtime configuration in `config.py`
- bot flow logic in `bot.py`
- user-facing copy in `texts.py`

That is not a full modular architecture yet, but it gives a cleaner starting point for a future refactor into provider adapters, generic chat capabilities, and pluggable model backends.

## Setup

```bash
git clone <your_repo_url>
cd banana-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file next to `bot.py`:

```env
TELEGRAM_BOT_TOKEN=your_token_from_@BotFather
OPENAI_API_KEY=your_key_from_OpenAI
XAI_API_KEY=your_key_from_xAI
ALLOWED_USERS=123456789,987654321

# Optional: webhook mode
WEBHOOK_URL=https://your-domain.com
PORT=8080

# Optional: persistent FSM storage
REDIS_URL=redis://localhost:6379/0
```

Run locally:

```bash
python bot.py
```

If `WEBHOOK_URL` is not set, the bot starts in long-polling mode. If it is set, the bot starts an `aiohttp` webhook server on the configured port.

## Notes

- This version does not yet provide generic free-form LLM chat.
- This version does not yet include public-user protections like quotas or rate limits.
- Do not commit `.env` with real secrets.
