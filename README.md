# 🍌 Banana Bot — Telegram Bot for Google Gemini

A friendly, fast, and secure Telegram bot for interacting with powerful **Google Gemini** generative models.

💡 **Key Features:**
- **Bilingual Support (English & Russian):** Native localization makes the bot accessible globally. Users can choose their language during the first launch and switch at any time. All texts are securely managed through a dedicated `texts.py` dictionary.
- **Stateless Architecture:** The bot does not keep chat context in memory, which significantly speeds up processing and saves API limits.
- **Clear Interface:** Intuitive buttons and neutral, straightforward text prompts make using the bot comfortable for users of any skill level.
- **Smart Processing:** The bot only accepts the necessary data formats (text, photo, audio) and cleanly reports internal errors or bad input.
- **Crash Protection:** Handled API errors (e.g., security constraints/rate limits), allowing you to restart or cancel operations cleanly at any time.
- **Easy Access to AI:** Harness Google Gemini's capabilities directly from Telegram without VPNs or complex interface configurations.

---

## 🛠 Capabilities

| Action / Button | Description |
|-------------------|-----------------|
| 🎨 **Generate Art** | Creates a unique image from scratch based on your text or speech. |
| 🪄 **Edit Photo** | Modifies a photo you provided according to any text or voice instruction. |
| 🌐 **Language / Язык** | Change the interface language between English (🇬🇧) and Russian (🇷🇺). |
| 💡 **Help** | Displays a brief reference guide to the bot's features. |
| ⚡️ / 💎 **Modes** | Switch seamlessly between maximum imaging detail (PRO) and maximum processing speed (FLASH). |

---

## 🧠 Available AI Models (Performance Modes)

Depending on the task, you can freely switch between two built-in configurations:

| Mode | Description | Background Logic Models |
|-------|---------|-------------------|
| 💎 **PRO** | High detail mode. It offers much higher picture resolution and better instructions alignment. Best suited for complex demands. | `gemini-3-pro-image-preview` (Draw & Edit)<br>`gemini-3-flash-preview` (Speech & Text Parsing) |
| ⚡️ **FLASH** | The turbo mode. Generation runs almost instantaneously, performing best for minor adjustments or simple sketch concepts. | `gemini-3.1-flash-image-preview` (Draw & Edit) <br>`gemini-3-flash-preview` (Speech & Text Parsing) |

---

## ⚙️ Tech Stack

This project is built atop modern and fully asynchronous tools:
- **[Aiogram 3.x](https://docs.aiogram.dev/)**: Flexible framework for asynchronous Telegram bots.
- **[Google GenAI SDK](https://github.com/google/genai-python)**: The official Google library abstracting new Gemini API standards.
- **Asyncio / Aiohttp**: Supports pure async streams making the codebase entirely non-blocking and webhook-ready.
- **Redis (Optional)**: Preserves state safely (FSM) over reboots. An in-memory cache acts as fallback when `REDIS_URL` isn't provided.
- **texts.py**: A clean dictionary-based localization system to abstract all user-facing strings out of the main logic block.

---

## 🚀 Setup & Installation Guide

Clone the repository and go to its root directory:
```bash
git clone <your_repo_url>
cd banana-bot
```

### 1. Set Up A Virtual Environment

Using an isolated environment for dependencies is highly advised.

**On macOS / Linux:**
```bash
# Create the environment
python3 -m venv venv

# Activate it
source venv/bin/activate
```
*(Use the `deactivate` command if you ever wish to exit.)*

**On Windows:**
```cmd
# Create the environment
python -m venv venv

# Activation (Command Prompt)
venv\Scripts\activate.bat

# Activation (PowerShell)
.\venv\Scripts\Activate.ps1
```

### 2. Install Package Dependencies

Make sure the environment remains open and activated, then securely grab dependencies:
```bash
pip install -r requirements.txt
```

### 3. Initialize Variables

Create a distinct `.env` file right beside your `bot.py` script mirroring the details below:
```env
TELEGRAM_BOT_TOKEN=your_token_from_@BotFather
GOOGLE_API_KEY=your_key_from_Google_AI_Studio
ALLOWED_USERS=123456789,987654321

# Webhook Deployments Only (Render, Heroku, VPS)
WEBHOOK_URL=https://your-domain.com
PORT=8080

# For FSM Persistency
REDIS_URL=redis://localhost:6379/0
```

### 4. Running the Bot

If your environment is engaged correctly:
```bash
python bot.py
```

> **Design Note:** The script defaults to pure *Long Polling* while run locally without a defined `WEBHOOK_URL`. If a `WEBHOOK_URL` enters `.env`, the script gracefully alters runtime logic pulling an aiohttp Webhook server automatically listening on the specified port.

### 5. Cleaning up (Uninstallation)

Should you want to discard the project structure entirely:
- Simply delete your virtual environment folder (`rm -rf venv` on Linux/Mac, or `rmdir /s /q venv` on Windows).
- Delete the folder housing the files.

---
*Remain Secure: Never commit `.env` containing your real keys into source version control systems (e.g. GitHub/Gitlab). A helpful `.gitignore` is provided within the repository specifically for ensuring safety.*