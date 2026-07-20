from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str
    options: Mapping[str, str] = field(default_factory=dict)

    def as_legacy_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model, **self.options}


def _target(value: str, **options: str) -> ModelTarget:
    provider, separator, model = value.partition(":")
    if not separator or not provider or not model:
        raise ConfigError(f"Invalid model target {value!r}; expected provider:model")
    return ModelTarget(provider.lower(), model, options)


def _chain(env: Mapping[str, str], name: str, default: str, **options: str) -> tuple[ModelTarget, ...]:
    raw = env.get(name, default)
    targets = tuple(_target(item.strip(), **options) for item in raw.split(",") if item.strip())
    if not targets:
        raise ConfigError(f"{name} must contain at least one provider:model target")
    return targets


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
    admin_users: frozenset[int]
    allowed_users: frozenset[int]
    chat_fast_chain: tuple[ModelTarget, ...]
    chat_balanced_chain: tuple[ModelTarget, ...]
    chat_complex_chain: tuple[ModelTarget, ...]
    image_pro_chain: tuple[ModelTarget, ...]
    image_fast_chain: tuple[ModelTarget, ...]
    transcription_chain: tuple[ModelTarget, ...]
    file_analysis_chain: tuple[ModelTarget, ...]
    speech_chain: tuple[ModelTarget, ...]
    speech_voice: str
    request_timeout_seconds: float
    connect_timeout_seconds: float
    http_retries: int
    rate_limit_per_minute: int
    max_output_tokens: int
    detailed_output_tokens: int
    memory_messages: int
    log_level: str

    @property
    def provider_keys(self) -> dict[str, str | None]:
        return {
            "openai": self.openai_api_key,
            "xai": self.xai_api_key,
            "google": self.google_api_key,
        }

    def enabled_chain(self, chain: tuple[ModelTarget, ...]) -> tuple[ModelTarget, ...]:
        return tuple(target for target in chain if self.provider_keys.get(target.provider))

    def validate(self) -> None:
        if not self.telegram_bot_token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is required")
        chains = {
            "CHAT_FAST_CHAIN": self.chat_fast_chain,
            "CHAT_BALANCED_CHAIN": self.chat_balanced_chain,
            "CHAT_COMPLEX_CHAIN": self.chat_complex_chain,
            "IMAGE_PRO_CHAIN": self.image_pro_chain,
            "IMAGE_FAST_CHAIN": self.image_fast_chain,
            "TRANSCRIPTION_CHAIN": self.transcription_chain,
            "FILE_ANALYSIS_CHAIN": self.file_analysis_chain,
        }
        supported = set(self.provider_keys)
        for name, chain in chains.items():
            unknown = {target.provider for target in chain} - supported
            if unknown:
                raise ConfigError(f"{name} contains unsupported providers: {sorted(unknown)}")
            if not self.enabled_chain(chain):
                raise ConfigError(f"{name} has no provider with a configured API key")
        speech_providers = {target.provider for target in self.speech_chain}
        unknown_speech = speech_providers - supported
        if unknown_speech:
            raise ConfigError(f"SPEECH_CHAIN contains unsupported providers: {sorted(unknown_speech)}")
        if self.memory_messages != 8:
            raise ConfigError("MEMORY_MESSAGES must be 8 to enforce the bounded-memory contract")
        if self.http_retries < 0 or self.rate_limit_per_minute < 1:
            raise ConfigError("HTTP_RETRIES and RATE_LIMIT_PER_MINUTE must be non-negative")


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(value.strip()) for value in raw.split(",") if value.strip().isdigit())


def load_config(env: Mapping[str, str] | None = None, *, validate: bool = False) -> AppConfig:
    values = os.environ if env is None else env
    allowed_raw = values.get("ALLOWED_USERS", "")
    image_options = {"quality": "medium", "size": "auto", "output_format": "jpeg"}
    config = AppConfig(
        telegram_bot_token=values.get("TELEGRAM_BOT_TOKEN"),
        openai_api_key=values.get("OPENAI_API_KEY"),
        xai_api_key=values.get("XAI_API_KEY"),
        google_api_key=values.get("GOOGLE_API_KEY"),
        allowed_users_env=allowed_raw,
        webhook_url=values.get("WEBHOOK_URL"),
        port=int(values.get("PORT", "8080")),
        redis_url=values.get("REDIS_URL"),
        admin_users=_ids(values.get("ADMIN_USERS", allowed_raw)),
        allowed_users=_ids(allowed_raw),
        chat_fast_chain=_chain(values, "CHAT_FAST_CHAIN", "openai:gpt-5.6-luna,google:gemini-3.5-flash"),
        chat_balanced_chain=_chain(values, "CHAT_BALANCED_CHAIN", "openai:gpt-5.6-terra,google:gemini-3.5-flash"),
        chat_complex_chain=_chain(values, "CHAT_COMPLEX_CHAIN", "openai:gpt-5.6-sol,google:gemini-3.5-pro"),
        image_pro_chain=_chain(values, "IMAGE_PRO_CHAIN", "openai:gpt-image-2,google:gemini-3.1-flash-image", **image_options),
        image_fast_chain=_chain(values, "IMAGE_FAST_CHAIN", "xai:grok-imagine-image-quality,google:gemini-3.1-flash-image", resolution="1k"),
        transcription_chain=_chain(values, "TRANSCRIPTION_CHAIN", "openai:gpt-4o-mini-transcribe,google:gemini-3.5-flash"),
        file_analysis_chain=_chain(values, "FILE_ANALYSIS_CHAIN", "openai:gpt-5.6-terra,google:gemini-3.5-flash"),
        speech_chain=_chain(values, "SPEECH_CHAIN", "openai:tts-1"),
        speech_voice=values.get("SPEECH_VOICE", "alloy"),
        request_timeout_seconds=float(values.get("REQUEST_TIMEOUT_SECONDS", "180")),
        connect_timeout_seconds=float(values.get("CONNECT_TIMEOUT_SECONDS", "10")),
        http_retries=int(values.get("HTTP_RETRIES", "2")),
        rate_limit_per_minute=int(values.get("RATE_LIMIT_PER_MINUTE", "20")),
        max_output_tokens=int(values.get("MAX_OUTPUT_TOKENS", "700")),
        detailed_output_tokens=int(values.get("DETAILED_OUTPUT_TOKENS", "1800")),
        memory_messages=int(values.get("MEMORY_MESSAGES", "8")),
        log_level=values.get("LOG_LEVEL", "INFO").upper(),
    )
    if validate:
        config.validate()
    return config
