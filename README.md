# Banana Mate 🍌

**A friendly AI assistant that lives in Telegram.** Ask questions, send voice messages, translate text, understand files, and create or edit images—without teaching anyone a new app.

Banana Mate is an open-source starter for developers who want to give family, friends, or customers a simple AI experience inside a familiar messenger.

<p align="center">
  <img src="assets/banana-mate-avatar.png" width="220" alt="Banana Mate avatar">
</p>

## What people can do

- ask everyday questions by text or voice;
- get a deeper answer for harder tasks;
- translate text and voice messages;
- summarize and explain files;
- create images or edit a photo;
- listen to answers as Telegram voice messages;
- start a fresh conversation at any time.

The interface is available in Russian and English. Answers are short by default; **More** expands them when needed.

## Fastest setup

You need Python 3.11+, a Telegram bot token from [@BotFather](https://t.me/BotFather), and API access to at least one provider.

```bash
git clone https://github.com/rustam-k0/banana-bot.git
cd banana-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

For the smallest working configuration, add these values to `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_token
GOOGLE_API_KEY=your_google_key
```

Add `OPENAI_API_KEY` for OpenAI chat, PRO images, transcription, and voice replies. Add `XAI_API_KEY` for fast image generation.

## Deploy on Render

1. Fork this repository.
2. Create a Render Blueprint from your fork. Render reads `render.yaml`.
3. Add `TELEGRAM_BOT_TOKEN` and provider keys in **Service → Environment**.
4. Deploy.

`/healthz` works in both webhook and polling modes. Local `.env` files are ignored and never uploaded to Render.

## Deploy on a VPS

The `production` branch can deploy automatically through GitHub Actions. The VPS runs the bot as an unprivileged `deploy` user under systemd, while API keys stay only in `/opt/banana-bot/.env`.

Deployment assets are in `deploy/`. A push to `production` runs the test suite, connects to the VPS with a dedicated SSH key, fast-forwards the checkout, installs dependencies, restarts the service, and verifies `/healthz`.

## Make it yours

Most adaptations only require three files:

| Change | File |
| --- | --- |
| Bot name, welcome text, buttons | `banana_bot/i18n.py` |
| Models, providers, limits | `.env` |
| Avatar | `assets/banana-mate-avatar.png` |

Suggested [@BotFather](https://t.me/BotFather) profile:

**Name**

`Banana Mate 🍌`

**Short description — RU**

`Дружелюбный AI-помощник в Telegram: вопросы, голос, перевод, файлы и изображения.`

**Short description — EN**

`A friendly Telegram AI for questions, voice, translation, files, and images.`

**Description — RU**

`Просто напишите или отправьте голосовое сообщение. Banana Mate поможет разобраться в вопросе, перевести текст, понять файл, создать изображение или изменить фото — прямо в Telegram.`

**Description — EN**

`Type or send a voice message. Banana Mate can answer questions, translate text, explain files, create images, and edit photos—right inside Telegram.`

The generated avatar is ready at [`assets/banana-mate-avatar.png`](assets/banana-mate-avatar.png).

## Models and fallbacks

Model chains use `provider:model,provider:model` syntax and are checked at startup.

| Task | Default route |
| --- | --- |
| Quick chat | OpenAI Luna → Google |
| Detailed chat and translation | OpenAI Terra → Google |
| Deep tasks | OpenAI Sol → Google |
| Fast images | xAI → Google |
| PRO images | GPT Image → Google |
| Voice input | OpenAI Transcribe → Google |
| Voice reply | OpenAI TTS |

Change any route in `.env`; see [`.env.example`](.env.example) for every option.

## Memory and privacy

The bot sends only a bounded context to providers:

- the latest 8 messages;
- a short rolling summary;
- facts explicitly saved with `Remember:` or `Запомни:`.

Safe structured logs contain model, latency, token, and error metadata—not prompts, files, API keys, or provider responses. `/admin_stats` shows process-local usage and approximate cost to users listed in `ADMIN_USERS`.

## Project map

```text
banana_bot/
├── adapters/       # OpenAI, xAI, Google
├── routers/        # Telegram commands and media flows
├── services/       # routing, retries, fallbacks
├── app.py          # startup, webhook, polling, health checks
├── config.py       # environment validation
├── i18n.py         # RU/EN content and button labels
├── memory.py       # bounded conversation context
└── observability.py
```

`bot.py` remains the stable entrypoint. Redis is optional and stores Telegram FSM state when `REDIS_URL` is set.

## Verify changes

Tests use mocked APIs and do not spend provider credits:

```bash
python -m unittest discover -s tests -v
```

Before making the bot public, review provider quotas, `ALLOWED_USERS`, `ADMIN_USERS`, token limits, and your privacy notice.
