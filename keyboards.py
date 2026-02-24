"""
keyboards.py — Клавиатуры и режимы пользователей.
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ── Режимы ───────────────────────────────────────────────

USER_MODES: dict[int, str] = {}

def get_mode(uid: int) -> str:
    return USER_MODES.get(uid, "flash")

def set_mode(uid: int, mode: str):
    USER_MODES[uid] = mode

# ── Тексты кнопок (= фильтры в handlers.py) ─────────────
# Эти тексты жёстко заданы в коде, чтобы не генерировать их через LLM
# и экономить вызовы к API (stateless UI).

BTN_DRAW   = "🎨 Арт"
BTN_EDIT   = "✏️ Изменить"
BTN_HELP   = "❓ Помощь"
BTN_CANCEL = "❌ Отмена"
BTN_MENU   = "🏠 В меню"
PRO_BTN    = "⚡ PRO"
FLASH_BTN  = "🟢 FLASH"

# ── Главная клавиатура ───────────────────────────────────

def main_kb(uid: int) -> ReplyKeyboardMarkup:
    mode_btn = PRO_BTN if get_mode(uid) == "pro" else FLASH_BTN
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DRAW), KeyboardButton(text=BTN_EDIT)],
            [KeyboardButton(text=mode_btn), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )

# ── Клавиатура отмены ────────────────────────────────────

CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_CANCEL), KeyboardButton(text=BTN_MENU)]],
    resize_keyboard=True,
)
