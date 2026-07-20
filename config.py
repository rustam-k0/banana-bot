"""Compatibility exports for deployments importing the legacy config module."""

from banana_bot.config import AppConfig, ModelTarget, load_config


_defaults = load_config(validate=False)
IMAGE_MODELS = {
    "PRO": [target.as_legacy_dict() for target in _defaults.image_pro_chain],
    "FLASH": [target.as_legacy_dict() for target in _defaults.image_fast_chain],
}
TRANSCRIPTION_MODELS = [
    target.as_legacy_dict() for target in _defaults.transcription_chain
]

__all__ = [
    "AppConfig",
    "ModelTarget",
    "IMAGE_MODELS",
    "TRANSCRIPTION_MODELS",
    "load_config",
]
