from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram import types
from aiogram.types import CallbackQuery, Message

from banana_bot.formatting import chunks, telegram_html
from banana_bot.http import ProviderError
from banana_bot.i18n import button_values, text
from banana_bot.keyboards import detail_keyboard, main_keyboard
from banana_bot.observability import log_event
from banana_bot.services.ai import AIService
from banana_bot.states import BotStates


def build_text_router(ai: AIService) -> Router:
    router = Router(name="text")
    modes = [
        ("BTN_CHAT", BotStates.chat, "CHAT_PROMPT", "fast"),
        ("BTN_COMPLEX", BotStates.complex_task, "COMPLEX_PROMPT", "complex"),
        ("BTN_GENERATE", BotStates.image_prompt, "GENERATE_PROMPT", None),
        ("BTN_EDIT", BotStates.photo_to_edit, "EDIT_PROMPT", None),
        ("BTN_FILE", BotStates.file_analysis, "FILE_PROMPT", None),
        ("BTN_TRANSLATE", BotStates.translation, "TRANSLATE_PROMPT", "balanced"),
    ]
    for button_key, target_state, prompt_key, requested_mode in modes:
        async def select_mode(message: Message, state: FSMContext, target=target_state, prompt=prompt_key, mode=requested_mode) -> None:
            data = await state.get_data()
            lang = data.get("lang", "EN")
            await state.set_state(target)
            if mode:
                await state.update_data(active_chat_mode=mode)
            await message.answer(text(lang, prompt), reply_markup=main_keyboard(lang))
        router.message.register(select_mode, F.text.in_(button_values(button_key)))

    async def send_result(message: Message, value: str, lang: str) -> None:
        parts = [telegram_html(part) for part in chunks(value)]
        for part in parts[:-1]:
            await message.answer(part)
        await message.answer(parts[-1], reply_markup=detail_keyboard(lang))

    async def run_chat(message: Message, state: FSMContext, content: str, forced_mode: str | None = None) -> None:
        data = await state.get_data()
        lang = data.get("lang", "EN")
        current = await state.get_state()
        mode = forced_mode or data.get("active_chat_mode") or data.get("chat_mode", "fast")
        if current == BotStates.translation.state:
            mode = "balanced"
            content = "Translate accurately. Preserve tone and formatting. " + content
        status = await message.answer(text(lang, "PROCESSING"))
        try:
            result = await ai.chat(message.from_user.id, content, mode)
            await state.update_data(last_request=content, last_mode=mode)
            await status.delete()
            await send_result(message, result.text, lang)
        except ProviderError as exc:
            key = "ERR_SAFETY" if exc.safety_related else "ERR_RATELIMIT" if exc.status == 429 else "ERR_SERVER" if exc.status >= 500 else "ERR_UNKNOWN"
            await status.edit_text(text(lang, key))

    @router.message(BotStates.chat, F.text)
    @router.message(BotStates.complex_task, F.text)
    @router.message(BotStates.translation, F.text)
    async def text_request(message: Message, state: FSMContext) -> None:
        current = await state.get_state()
        forced = "complex" if current == BotStates.complex_task.state else None
        await run_chat(message, state, message.text or "", forced)

    @router.callback_query(F.data == "answer:detail")
    async def detail(callback: CallbackQuery, state: FSMContext) -> None:
        log_event("callback_received", action="detail", user_id=callback.from_user.id)
        data = await state.get_data()
        lang = data.get("lang", "EN")
        if not data.get("last_request") or not callback.message:
            await callback.answer(text(lang, "NO_DETAIL"), show_alert=True)
            return
        await callback.answer()
        prompt = data["last_request"] + "\n\n" + text(lang, "DETAIL_PROMPT")
        status = await callback.message.answer(text(lang, "PROCESSING"))
        try:
            result = await ai.chat(callback.from_user.id, prompt, data.get("last_mode", "balanced"), detailed=True)
            await status.delete()
            await send_result(callback.message, result.text, lang)
        except ProviderError as exc:
            log_event("callback_failure", action="detail", status=exc.status, code=exc.code)
            await status.edit_text(text(lang, "ERR_SERVER"))
        except Exception as exc:
            log_event("callback_failure", action="detail", error_type=type(exc).__name__)
            await status.edit_text(text(lang, "ERR_SERVER"))

    @router.callback_query(F.data == "answer:speak")
    async def speak(callback: CallbackQuery, state: FSMContext) -> None:
        log_event("callback_received", action="speak", user_id=callback.from_user.id)
        data = await state.get_data()
        lang = data.get("lang", "EN")
        if not callback.message or not callback.message.text:
            await callback.answer(text(lang, "VOICE_ERROR"), show_alert=True)
            return
        await callback.answer()
        status = await callback.message.answer(text(lang, "VOICE_PROCESSING"))
        try:
            result = await ai.synthesize(callback.message.text)
            await callback.message.answer_voice(types.BufferedInputFile(result.content, filename="answer.ogg"))
            await status.delete()
        except ProviderError as exc:
            log_event("callback_failure", action="speak", status=exc.status, code=exc.code)
            await status.edit_text(text(lang, "VOICE_ERROR"))
        except Exception as exc:
            log_event("callback_failure", action="speak", error_type=type(exc).__name__)
            await status.edit_text(text(lang, "VOICE_ERROR"))

    return router
