import asyncio
import logging
import os
import base64
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import AsyncGroq
from aiohttp import web

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

SYSTEM_PROMPT = "Ты — умный ассистент. Отвечай на вопросы пользователя, анализируя текст и изображения. Если на фото есть текст, можешь его прочитать или переписать."

# --- Веб-сервер для Render (оставляем, чтобы бот не засыпал) ---
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

# --- Обработчик текста (как раньше) ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Отправь мне текст или фотографию, и я помогу с ним разобраться.")

@dp.message(types.ContentType.TEXT)
async def handle_text(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b", # Для текста оставляем быструю модель
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message.text}],
            temperature=0.3, max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply("Ошибка при обработке.")

# --- НОВЫЙ обработчик для ФОТО ---
@dp.message(types.ContentType.PHOTO)
async def handle_photo(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Берем фото в максимальном качестве
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    # Переводим картинку в base64 (формат, который понимает Groq)
    image_bytes = downloaded_file.read()
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    # Вопрос пользователя (подпись к фото, если есть, иначе стандартный)
    user_question = message.caption if message.caption else "Что на этой фотографии? Если есть текст, прочитай его."
    
    try:
        # Используем мультимодальную модель (например, Qwen или Llama 4)
        response = await client.chat.completions.create(
            model="qwen/qwen3.8-27b", # Мультимодальная модель Groq
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
        logging.error(f"Ошибка при обработке фото: {e}")
        await message.reply("Не удалось обработать фото. Попробуй еще раз.")

async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
