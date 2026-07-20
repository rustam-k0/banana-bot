# 🍌 Banana Bot

Production-oriented Telegram assistant built with Python and aiogram 3. It supports concise chat, complex tasks, image generation and editing, file analysis, translation, voice input, bounded conversation memory, provider fallback, and operational statistics.

![Banana Bot result demo](assets/telegram-demo-result.jpg)

## User flows

- `💬 Chat` — concise answers through the fast chat chain (Luna by default).
- `🧠 Complex task` — deeper reasoning through the complex chain (Sol by default).
- `🎨 Generate image` — fast xAI images or PRO images through GPT Image.
- `🪄 Edit image` — edit a Telegram photo using the selected image quality.
- `📎 Analyze file` — summarize a document and extract facts, risks, and actions.
- `🌐 Translate` — balanced translation through Terra by default.
- `🆕 New conversation` — clear messages, rolling summary, and saved facts.
- `⚙️ Settings` — choose fast/balanced/complex chat and fast/PRO images.
- `Подробнее / More` — request an expanded answer with a larger output limit.

## Model routing

Every chain is configurable as a comma-separated list of `provider:model` targets. Providers without an API key are skipped. Configuration is validated before Telegram polling or webhook startup; startup fails clearly when a required chain has no usable provider.

| Workload | Default primary | Default fallback |
| --- | --- | --- |
| Regular chat | OpenAI `gpt-5.6-luna` | Google `gemini-3.5-flash` |
| Balanced chat / translation | OpenAI `gpt-5.6-terra` | Google `gemini-3.5-flash` |
| Complex tasks | OpenAI `gpt-5.6-sol` | Google `gemini-3.5-pro` |
| PRO image | OpenAI `gpt-image-2` | Google `gemini-3.1-flash-image` |
| Fast image | xAI `grok-imagine-image-quality` | Google `gemini-3.1-flash-image` |
| Transcription | OpenAI `gpt-4o-mini-transcribe` | Google `gemini-3.5-flash` |

Safety-policy errors do not fall through to another provider. Transient HTTP failures, timeouts, rate limits, and empty provider responses do.

## Memory and response limits

Conversation context contains only:

- the latest 8 user/assistant messages;
- a bounded rolling summary of evicted messages;
- up to 20 explicitly saved facts (`Remember: …` / `Запомни: …`).

Normal answers use `MAX_OUTPUT_TOKENS=700`; the More button uses `DETAILED_OUTPUT_TOKENS=1800`. New conversation clears all three memory layers.

## Architecture

```text
banana_bot/
├── adapters/          # OpenAI, xAI, and Google wire formats
├── routers/           # common, text, media, and admin Telegram routing
├── services/          # provider-independent orchestration and fallback
├── app.py             # dependency wiring, polling, and webhook lifecycle
├── config.py          # environment parsing and startup validation
├── http.py            # shared async client, timeout, retry, rate limit
├── i18n.py            # RU/EN interface text
├── memory.py          # bounded messages, summary, saved facts
└── observability.py   # structured safe logs and in-process metrics
```

The top-level `bot.py`, `config.py`, and `texts.py` remain compatibility entrypoints for existing deployments and imports.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Existing variables remain supported: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `XAI_API_KEY`, `GOOGLE_API_KEY`, `ALLOWED_USERS`, `WEBHOOK_URL`, `PORT`, and `REDIS_URL`.

If `WEBHOOK_URL` is absent, the bot uses long polling. If `ALLOWED_USERS` is empty, access is public. `ADMIN_USERS` defaults to the whitelist when omitted.

## Observability and admin stats

Logs are structured and include operation, provider, model, status, and error type. Prompt/file contents, authorization headers, API keys, and provider error bodies are not logged.

Administrators can run `/admin_stats` to see:

- active user count and request volume;
- calls and errors by provider/model;
- average latency;
- input/output token totals;
- approximate cost (an estimate, not a billing source of truth).

Metrics are process-local and reset on restart. Redis is currently used for Telegram FSM persistence; the conversation-memory interface is intentionally storage-agnostic so a persistent implementation can be added without changing routers.

## Tests

The suite uses mocked provider APIs and never needs real keys:

```bash
python -m unittest discover -s tests -v
```

It covers configuration compatibility and validation, exact memory bounds, model selection, fallback and safety behavior, OpenAI/Google payload contracts, and Telegram router wiring.

## Deployment notes

- Keep `.env` out of version control; it is ignored by this repository.
- Render does not receive your local `.env`. Add `TELEGRAM_BOT_TOKEN` and the provider keys in **Service → Environment**. At least one configured provider must be available in every model chain.
- [`render.yaml`](render.yaml) declares the build/start commands, secret placeholders, Python version, and `/healthz` check. Polling mode also opens `PORT`, so a Render web service remains healthy without webhook mode.
- Set `REDIS_URL` for persistent FSM state across restarts or multiple workers.
- Tune `RATE_LIMIT_PER_MINUTE`, timeouts, retries, and token limits for provider quotas.
- The shared rate limit is per bot process. Use an external distributed limiter when running multiple replicas.
