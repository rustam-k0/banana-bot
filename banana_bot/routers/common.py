from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from banana_bot.i18n import TEXTS, button_values, text
from banana_bot.keyboards import language_keyboard, main_keyboard, settings_keyboard
from banana_bot.memory import ConversationMemory
from banana_bot.states import BotStates


def build_common_router(memory: ConversationMemory) -> Router:
    router = Router(name="common")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        lang = data.get("lang")
        await state.clear()
        if not lang:
            await state.set_state(BotStates.language)
            await message.answer(TEXTS["EN"]["CHOOSE_LANG"], reply_markup=language_keyboard())
            return
        await state.update_data(lang=lang, image_mode="FLASH", chat_mode="fast")
        await message.answer(text(lang, "WELCOME"), reply_markup=main_keyboard(lang))

    @router.message(F.text.in_(button_values("BTN_LANG")))
    async def choose_language(message: Message, state: FSMContext) -> None:
        await state.set_state(BotStates.language)
        await message.answer(TEXTS["EN"]["CHOOSE_LANG"], reply_markup=language_keyboard())

    @router.message(BotStates.language, F.text.in_({"English 🇬🇧", "Русский 🇷🇺"}))
    async def language_selected(message: Message, state: FSMContext) -> None:
        lang = "EN" if message.text and message.text.startswith("English") else "RU"
        await state.set_state(None)
        await state.update_data(lang=lang, image_mode="FLASH", chat_mode="fast")
        await message.answer(text(lang, "LANG_SET"), reply_markup=main_keyboard(lang))
        await message.answer(text(lang, "WELCOME"), reply_markup=main_keyboard(lang))

    @router.message(BotStates.language)
    async def invalid_language(message: Message) -> None:
        await message.answer(TEXTS["EN"]["INVALID_LANG"] + " / " + TEXTS["RU"]["INVALID_LANG"], reply_markup=language_keyboard())

    @router.message(F.text.in_(button_values("BTN_NEW")))
    async def new_dialog(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        lang = data.get("lang", "EN")
        memory.clear(message.from_user.id)
        await state.set_state(None)
        await state.update_data(lang=lang, image_mode=data.get("image_mode", "FLASH"), chat_mode=data.get("chat_mode", "fast"))
        await message.answer(text(lang, "NEW_DIALOG"), reply_markup=main_keyboard(lang))

    @router.message(F.text.in_(button_values("BTN_SETTINGS")))
    async def settings(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        lang = data.get("lang", "EN")
        await state.set_state(BotStates.settings)
        await message.answer(text(lang, "SETTINGS_TEXT"), reply_markup=settings_keyboard(lang))

    settings_map = {
        **{value: ("chat_mode", "fast") for value in button_values("BTN_FAST_CHAT")},
        **{value: ("chat_mode", "balanced") for value in button_values("BTN_BALANCED")},
        **{value: ("chat_mode", "complex") for value in button_values("BTN_COMPLEX_CHAT")},
        **{value: ("image_mode", "PRO") for value in button_values("BTN_PRO")},
        **{value: ("image_mode", "FLASH") for value in button_values("BTN_FLASH")},
    }

    @router.message(F.text.in_(set(settings_map)))
    async def setting_selected(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        lang = data.get("lang", "EN")
        key, value = settings_map[message.text]
        await state.update_data(**{key: value})
        if key == "chat_mode":
            await state.set_state(BotStates.chat)
            await message.answer(text(lang, "MODE_SET", mode=value), reply_markup=main_keyboard(lang))
            await message.answer(text(lang, "CHAT_PROMPT"), reply_markup=main_keyboard(lang))
        else:
            await message.answer(text(lang, "MODE_SET", mode=value), reply_markup=settings_keyboard(lang))

    @router.message(F.text.in_(button_values("BTN_HELP")))
    async def help_message(message: Message, state: FSMContext) -> None:
        lang = (await state.get_data()).get("lang", "EN")
        await message.answer(text(lang, "HELP_TEXT"), reply_markup=main_keyboard(lang))

    return router
