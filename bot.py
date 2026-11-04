import asyncio
import logging
import os
import io
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import FSInputFile
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from groq import AsyncGroq
from aiohttp import web

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Работа с текстом")
    builder.button(text="🤖 Задать вопрос")
    builder.button(text="🎨 Фильтры для фото")
    builder.adjust(3)
    return builder.as_markup(resize_keyboard=True)

def get_filters_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Ч/Б (Черно-белое)", callback_data="filter_bw")
    builder.button(text="Размытие", callback_data="filter_blur")
    builder.button(text="Винтаж", callback_data="filter_vintage")
    builder.button(text="Контраст", callback_data="filter_contrast")
    builder.adjust(2)
    return builder.as_markup()

# Словарь для запоминания режима
user_modes = {}

# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Выбери, что будем делать 👇",
        reply_markup=get_main_keyboard()
    )

# --- ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ ---
@dp.message(F.text == "📝 Работа с текстом")
async def mode_text(message: types.Message):
    user_modes[message.from_user.id] = "text"
    await message.answer("Режим редактора. Отправь текст, и я его обработаю.", reply_markup=get_main_keyboard())

@dp.message(F.text == "🤖 Задать вопрос")
async def mode_question(message: types.Message):
    user_modes[message.from_user.id] = "question"
    await message.answer("Режим вопросов. Напиши свой вопрос, и я отвечу.", reply_markup=get_main_keyboard())

@dp.message(F.text == "🎨 Фильтры для фото")
async def mode_filters(message: types.Message):
    user_modes[message.from_user.id] = "filter"
    await message.answer("Выбери фильтр, а затем отправь фото:", reply_markup=get_filters_keyboard())

# --- ОБРАБОТКА ФИЛЬТРОВ (CALLBACK) ---
@dp.callback_query(F.data.startswith("filter_"))
async def process_filter_choice(callback: types.CallbackQuery):
    filter_type = callback.data.split("_")[1]
    # Сохраняем выбранный фильтр в словарь для этого пользователя
    # (можно использовать отдельный словарь, но для простоты запомним в режиме)
    user_modes[callback.from_user.id] = f"filter_{filter_type}"
    await callback.message.answer(f"Фильтр выбран! Теперь отправь мне фото, и я его обработаю.")
    await callback.answer()

# --- ОБРАБОТКА ТЕКСТА (РЕДАКТОР/ВОПРОСЫ) ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text in ["📝 Работа с текстом", "🤖 Задать вопрос", "🎨 Фильтры для фото"]:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    mode = user_modes.get(message.from_user.id, "text")

    if mode == "question":
        sys_prompt = "Ты — умный ассистент. Отвечай на вопрос пользователя точно, по делу."
        temp = 0.5
    else:
        sys_prompt = "Ты — профессиональный редактор. Сокращай и редактируй текст."
        temp = 0.3

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": message.text}],
            temperature=temp, max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка текста: {e}")
        await message.reply("Ошибка при обработке текста.")

# --- ОБРАБОТКА ФОТО (ФИЛЬТРЫ) ---
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    mode = user_modes.get(message.from_user.id, "")
    
    # Если пользователь не в режиме фильтров, просто говорим ему выбрать фильтр
    if not mode.startswith("filter_"):
        await message.reply("Сначала выбери фильтр в меню '🎨 Фильтры для фото', а потом отправь фото!")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    
    # Скачиваем фото
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    # Открываем изображение через Pillow
    image = Image.open(io.BytesIO(downloaded_file.read()))
    
    # Применяем фильтр
    filter_type = mode.split("_")[1]
    if filter_type == "bw":
        image = ImageOps.grayscale(image)
    elif filter_type == "blur":
        image = image.filter(ImageFilter.GaussianBlur(radius=5))
    elif filter_type == "vintage":
        # Простой эффект винтажа: сепия
        image = ImageOps.grayscale(image)
        image = ImageOps.colorize(image, black="#2c1b18", white="#e0c9a6")
    elif filter_type == "contrast":
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0) # Увеличиваем контраст в 2 раза

    # Сохраняем результат в память
    output = io.BytesIO()
    image.save(output, format='JPEG')
    output.seek(0)
    
    # Отправляем обратно
    await message.reply_photo(photo=FSInputFile(output, filename="result.jpg"), caption="Готово! Твой фильтр применен.")
    
    # Сбрасываем режим, чтобы не применять фильтр случайно
    user_modes[message.from_user.id] = "text"

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
