from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AppConfig:
    telegram_bot_token: str | None
    openai_api_key: str | None
    xai_api_key: str | None
    google_api_key: str | None
    allowed_users_env: str
    webhook_url: str | None
    port: int
    redis_url: str | None


IMAGE_MODELS = {
    "PRO": [
        {
            "provider": "openai",
            "model": "gpt-image-2",
            "quality": "medium",
            "size": "auto",
            "output_format": "jpeg",
        },
        {
            "provider": "google",
            "model": "gemini-3.1-flash-image",
        },
    ],
    "FLASH": [
        {
            "provider": "xai",
            "model": "grok-imagine-image-quality",
            "resolution": "1k",
        },
        {
            "provider": "google",
            "model": "gemini-3.1-flash-image",
        },
    ],
}

TRANSCRIPTION_MODELS = [
    {"provider": "openai", "model": "gpt-4o-mini-transcribe"},
    {"provider": "google", "model": "gemini-3.5-flash"},
]


def load_config() -> AppConfig:
    return AppConfig(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        xai_api_key=os.getenv("XAI_API_KEY"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        allowed_users_env=os.getenv("ALLOWED_USERS", ""),
        webhook_url=os.getenv("WEBHOOK_URL"),
        port=int(os.getenv("PORT", 8080)),
        redis_url=os.getenv("REDIS_URL"),
    )
