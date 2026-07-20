from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from banana_bot.formatting import chunks, telegram_html
from banana_bot.http import ProviderError
from banana_bot.i18n import text
from banana_bot.keyboards import detail_keyboard
from banana_bot.services.ai import AIService
from banana_bot.states import BotStates


MAX_FILE_BYTES = 20 * 1024 * 1024


async def _download(bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    stream = await bot.download_file(file.file_path)
    return stream.read()


def build_media_router(ai: AIService) -> Router:
    router = Router(name="media")

    @router.message(BotStates.image_prompt, F.text)
    async def generate(message: Message, state: FSMContext) -> None:
        data = await state.get_data(); lang = data.get("lang", "EN")
        status = await message.answer(text(lang, "PROCESS_GEN_START"))
        try:
            result = await ai.generate_image(message.text or "", data.get("image_mode", "FLASH"))
            await message.answer_photo(types.BufferedInputFile(result.content, filename="art.jpg"))
            await status.delete()
        except ProviderError as exc:
            await status.edit_text(text(lang, "ERR_SAFETY" if exc.safety_related else "ERR_SERVER"))

    @router.message(BotStates.photo_to_edit, F.photo)
    async def photo(message: Message, state: FSMContext) -> None:
        lang = (await state.get_data()).get("lang", "EN")
        await state.update_data(edit_photo_file_id=message.photo[-1].file_id)
        await state.set_state(BotStates.edit_prompt)
        await message.answer(text(lang, "PHOTO_LOADED_PROMPT"))

    @router.message(BotStates.edit_prompt, F.text)
    async def edit(message: Message, state: FSMContext, bot) -> None:
        data = await state.get_data(); lang = data.get("lang", "EN")
        status = await message.answer(text(lang, "PROCESS_EDIT_PREP"))
        try:
            image = await _download(bot, data["edit_photo_file_id"])
            await status.edit_text(text(lang, "PROCESS_EDIT_GEN"))
            result = await ai.edit_image(image, message.text or "", data.get("image_mode", "FLASH"))
            await message.answer_photo(types.BufferedInputFile(result.content, filename="edited.jpg"))
            await status.delete()
            await state.update_data(edit_photo_file_id=None)
        except ProviderError as exc:
            await status.edit_text(text(lang, "ERR_SAFETY" if exc.safety_related else "ERR_SERVER"))
        except Exception:
            await status.edit_text(text(lang, "ERR_DL_TELEGRAM"))

    @router.message(BotStates.file_analysis, F.document)
    async def file_analysis(message: Message, state: FSMContext, bot) -> None:
        data = await state.get_data(); lang = data.get("lang", "EN")
        if message.document.file_size and message.document.file_size > MAX_FILE_BYTES:
            await message.answer(text(lang, "ERR_FILE_TOO_LARGE")); return
        status = await message.answer(text(lang, "PROCESS_FILE"))
        try:
            content = await _download(bot, message.document.file_id)
            result = await ai.analyze_file(content, message.document.mime_type or "application/octet-stream", "Analyze this file. Summarize it and list key facts, risks, and next actions. Answer in the user's language.")
            await status.delete()
            for part in chunks(result.text):
                await message.answer(telegram_html(part))
        except ProviderError:
            await status.edit_text(text(lang, "ERR_SERVER"))
        except Exception:
            await status.edit_text(text(lang, "ERR_DL_TELEGRAM"))

    @router.message(F.voice)
    async def voice(message: Message, state: FSMContext, bot) -> None:
        data = await state.get_data(); lang = data.get("lang", "EN")
        if await state.get_state() == BotStates.photo_to_edit.state:
            await message.answer(text(lang, "VOICE_NO_PHOTO")); return
        status = await message.answer(text(lang, "PROCESS_VOICE_RX"))
        try:
            audio = await _download(bot, message.voice.file_id)
            await status.edit_text(text(lang, "PROCESS_VOICE_TRANS"))
            result = await ai.transcribe(audio)
            await status.delete()
            await message.answer(text(lang, "TXT_TRANSCRIBED", text=telegram_html(result.text)))
            current = await state.get_state()
            if current == BotStates.image_prompt.state:
                generated = await ai.generate_image(result.text, data.get("image_mode", "FLASH"))
                await message.answer_photo(types.BufferedInputFile(generated.content, filename="art.jpg"))
            elif current == BotStates.edit_prompt.state:
                image = await _download(bot, data["edit_photo_file_id"])
                edited = await ai.edit_image(image, result.text, data.get("image_mode", "FLASH"))
                await message.answer_photo(types.BufferedInputFile(edited.content, filename="edited.jpg"))
            elif current in {BotStates.chat.state, BotStates.complex_task.state, BotStates.translation.state}:
                mode = "complex" if current == BotStates.complex_task.state else "balanced" if current == BotStates.translation.state else data.get("chat_mode", "fast")
                answer = await ai.chat(message.from_user.id, result.text, mode)
                await state.update_data(last_request=result.text, last_mode=mode)
                parts = chunks(answer.text)
                for part in parts[:-1]:
                    await message.answer(telegram_html(part))
                await message.answer(telegram_html(parts[-1]), reply_markup=detail_keyboard(lang))
            else:
                await message.answer(text(lang, "ERR_MENU_FIRST"))
        except Exception:
            await status.edit_text(text(lang, "ERR_SERVER"))

    @router.message(F.photo)
    async def unexpected_photo(message: Message, state: FSMContext) -> None:
        lang = (await state.get_data()).get("lang", "EN")
        await message.answer(text(lang, "ERR_PHOTO_NO_MENU"))

    @router.message()
    async def unsupported(message: Message, state: FSMContext) -> None:
        lang = (await state.get_data()).get("lang", "EN")
        current = await state.get_state()
        key = "ERR_NEED_PHOTO_NOT_TEXT" if current == BotStates.photo_to_edit.state else "ERR_UNSUPPORTED_MEDIA" if current else "ERR_MENU_FIRST"
        await message.answer(text(lang, key))

    return router
