import asyncio
import io
import logging
import os
import textwrap
from PIL import Image

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ALLOWED_USERS_STR = os.getenv("ALLOWED_USERS", "")

ALLOWED_USERS = set()
for uid in ALLOWED_USERS_STR.split(","):
    uid = uid.strip()
    if uid.isdigit():
        ALLOWED_USERS.add(int(uid))

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def send_long_message(message: Message, text: str):
    if not text:
        return
    for chunk in textwrap.wrap(text, width=4000, replace_whitespace=False, drop_whitespace=False):
        await message.answer(chunk)

@dp.message(~F.from_user.id.in_(ALLOWED_USERS))
async def unauthorized_access(message: Message):
    logger.warning(f"Неавторизованная попытка доступа от пользователя: {message.from_user.id}")
    await message.answer(f"⛔️ Доступ запрещен.\nВаш Telegram ID: `{message.from_user.id}`\n\nПожалуйста, добавьте этот ID в параметр `ALLOWED_USERS` в файле `.env` и перезапустите бота.", parse_mode="Markdown")
    return

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я твой ИИ-ассистент на базе Google Gemini.\n\n"
        "Отправь мне текст, фото или используй `/img <запрос>` для генерации картинки.",
        parse_mode="Markdown"
    )

@dp.message(Command("img"))
async def handle_image_generation(message: Message):
    prompt = message.text.replace("/img", "", 1).strip()
    if not prompt:
        await message.answer("Пожалуйста, укажите запрос. Пример: `/img киберпанк город`", parse_mode="Markdown")
        return

    status_msg = await message.answer("🎨 Генерирую картинку...")
    try:
        imagen = genai.ImageGenerationModel("imagen-3.0-generate-001")
        result = imagen.generate_images(prompt=prompt, number_of_images=1, aspect_ratio="1:1")
        
        for generated_image in result.images:
            img_byte_arr = io.BytesIO()
            generated_image.image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            photo = BufferedInputFile(img_byte_arr.getvalue(), filename="generated.png")
            await message.reply_photo(photo=photo, caption=f"Запрос: {prompt}")
            break
            
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        error_str = str(e).lower()
        if "timeout" in error_str:
            await status_msg.edit_text("❌ Запрос превысил время ожидания. Попробуйте позже.")
        elif "safety" in error_str or "block" in error_str:
            await status_msg.edit_text("❌ Запрос заблокирован фильтрами безопасности.")
        elif "quota" in error_str or "limit" in error_str:
            await status_msg.edit_text("❌ Лимит запросов исчерпан. Попробуйте позже.")
        else:
            await status_msg.edit_text("❌ Произошла ошибка при генерации изображения.")

@dp.message(F.photo)
async def handle_photo(message: Message):
    status_msg = await message.answer("👁️ Анализирую изображение...")
    try:
        photo_info = message.photo[-1]
        file_info = await bot.get_file(photo_info.file_id)
        
        downloaded_file = await bot.download_file(file_info.file_path)
        img = Image.open(downloaded_file)
        
        prompt = message.caption if message.caption else "Опиши это изображение в деталях."
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content([prompt, img])
        
        await status_msg.delete()
        await send_long_message(message, response.text)
        
    except Exception as e:
        logger.error(f"Vision analysis failed: {e}")
        error_str = str(e).lower()
        if "timeout" in error_str:
            await status_msg.edit_text("❌ Запрос превысил время ожидания.")
        elif "safety" in error_str or "block" in error_str:
            await status_msg.edit_text("❌ Изображение заблокировано фильтрами безопасности.")
        else:
            await status_msg.edit_text("❌ Ошибка при обработке изображения.")

@dp.message(F.text)
async def handle_text(message: Message):
    status_msg = await message.answer("💬 Думаю...")
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(message.text)
        
        await status_msg.delete()
        await send_long_message(message, response.text)
        
    except Exception as e:
        logger.error(f"Text generation failed: {e}")
        error_str = str(e).lower()
        if "timeout" in error_str:
            await status_msg.edit_text("❌ Запрос превысил время ожидания.")
        elif "safety" in error_str or "block" in error_str:
            await status_msg.edit_text("❌ Сообщение заблокировано фильтрами безопасности.")
        elif "quota" in error_str or "limit" in error_str:
            await status_msg.edit_text("❌ Лимит запросов исчерпан. Попробуйте позже.")
        else:
            await status_msg.edit_text("❌ Ошибка при генерации ответа.")

async def main():
    if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEY:
        logger.error("Отсутствуют необходимые API ключи в .env")
        return
    if not ALLOWED_USERS:
        logger.warning("Список ALLOWED_USERS пуст! Бот будет игнорировать всех.")
        
    logger.info("Бот запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
