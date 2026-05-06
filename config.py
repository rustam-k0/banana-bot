from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AppConfig:
    telegram_bot_token: str | None
    google_api_key: str | None
    allowed_users_env: str
    webhook_url: str | None
    port: int
    redis_url: str | None


IMAGE_GEN_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-3.1-flash-image-preview"],
}

IMAGE_EDIT_MODELS = {
    "PRO": ["gemini-3-pro-image-preview"],
    "FLASH": ["gemini-3.1-flash-image-preview"],
}

TEXT_AUDIO_MODELS = {
    "PRO": ["gemini-3-flash-preview"],
    "FLASH": ["gemini-3-flash-preview"],
}


def load_config() -> AppConfig:
    return AppConfig(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        allowed_users_env=os.getenv("ALLOWED_USERS", ""),
        webhook_url=os.getenv("WEBHOOK_URL"),
        port=int(os.getenv("PORT", 8080)),
        redis_url=os.getenv("REDIS_URL"),
    )
