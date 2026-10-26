import asyncio
import logging
import os
import base64
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from groq import AsyncGroq
from aiohttp import web

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(
    api_key=GROQ_API_KEY,
    http_client=httpx.AsyncClient(verify=False)
)

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

# --- КНОПКИ МЕНЮ ---
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Работа с текстом/фото")
    builder.button(text="🤖 Задать вопрос")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

# Словарь для запоминания режима пользователя
user_modes = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Выбери режим работы, нажав на кнопку внизу 👇",
        reply_markup=get_main_keyboard()
    )

# --- ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ ---
@dp.message(F.text == "📝 Работа с текстом/фото")
async def mode_text(message: types.Message):
    user_modes[message.from_user.id] = "text"
    await message.answer("Режим редактора. Отправь текст или фото, и я обработаю.", reply_markup=get_main_keyboard())

@dp.message(F.text == "🤖 Задать вопрос")
async def mode_question(message: types.Message):
    user_modes[message.from_user.id] = "question"
    await message.answer("Режим вопросов. Напиши свой вопрос, и я отвечу.", reply_markup=get_main_keyboard())

# --- ОБРАБОТКА ТЕКСТА ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    # Игнорируем нажатия на кнопки меню
    if message.text in ["📝 Работа с текстом/фото", "🤖 Задать вопрос"]:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    mode = user_modes.get(message.from_user.id, "text")

    if mode == "question":
        sys_prompt = "Ты — умный ассистент. Отвечай на вопрос пользователя точно, по делу, без лишней воды."
        temp = 0.5
    else:
        sys_prompt = "Ты — профессиональный редактор. Сокращай и редактируй текст. Отвечай только готовым текстом."
        temp = 0.3

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b", # Текстовая модель
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": message.text}
            ],
            temperature=temp, 
            max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка текста: {e}")
        await message.reply("Ошибка при обработке текста.")

# --- ОБРАБОТКА ФОТО (ИСПРАВЛЕНО) ---
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Берем фото в максимальном качестве
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    
    # Скачиваем файл в память (без сохранения на диск)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    # Переводим в base64
    image_bytes = downloaded_file.read()
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    # Вопрос из подписи к фото или стандартный
    user_question = message.caption if message.caption else "Что на этой фотографии? Если есть текст, прочитай его."
    
    try:
        # Используем мультимодальную модель Qwen
        response = await client.chat.completions.create(
            model="qwen/qwen3.8-27b", 
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }
            ],
            temperature=0.3,
            max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await message.reply("Не удалось обработать фото. Убедись, что оно меньше 20 МБ, или попробуй позже.")

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    print("Бот запущен...")
    await dp.start_polling(bot)

if _name_ == "_main_":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())
