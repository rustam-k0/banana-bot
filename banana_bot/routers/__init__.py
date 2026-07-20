from banana_bot.routers.admin import build_admin_router
from banana_bot.routers.common import build_common_router
from banana_bot.routers.media import build_media_router
from banana_bot.routers.text import build_text_router

__all__ = ["build_admin_router", "build_common_router", "build_media_router", "build_text_router"]
