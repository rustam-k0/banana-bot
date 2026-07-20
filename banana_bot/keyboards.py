from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from banana_bot.i18n import TEXTS


def main_keyboard(lang: str) -> ReplyKeyboardMarkup:
    t = TEXTS[lang]
    rows = [
        [t["BTN_CHAT"], t["BTN_COMPLEX"]],
        [t["BTN_GENERATE"], t["BTN_EDIT"]],
        [t["BTN_FILE"], t["BTN_TRANSLATE"]],
        [t["BTN_NEW"], t["BTN_SETTINGS"]],
    ]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=value) for value in row] for row in rows], resize_keyboard=True)


def language_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="English 🇬🇧"), KeyboardButton(text="Русский 🇷🇺")]], resize_keyboard=True, one_time_keyboard=True)


def settings_keyboard(lang: str) -> ReplyKeyboardMarkup:
    t = TEXTS[lang]
    rows = [[t["BTN_FAST_CHAT"], t["BTN_BALANCED"]], [t["BTN_COMPLEX_CHAT"]], [t["BTN_PRO"], t["BTN_FLASH"]], [t["BTN_LANG"], t["BTN_HELP"]], [t["BTN_NEW"]]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=value) for value in row] for row in rows], resize_keyboard=True)


def detail_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=TEXTS[lang]["DETAIL_EN"] if lang == "EN" else TEXTS[lang]["DETAIL"], callback_data="answer:detail")]])
