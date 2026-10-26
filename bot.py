import asyncio
import logging
import os
import base64
import httpx
from aiogram import Bot, Dispatcher, types, F  # Добавлен F для фильтров
from aiogram.filters import Command
from groq import AsyncGroq
from aiohttp import web

# Получаем ключи из настроек Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Клиент Groq с отключенной проверкой SSL (чтобы Render не блокировал)
client = AsyncGroq(
    api_key=GROQ_API_KEY,
    http_client=httpx.AsyncClient(verify=False)
)

SYSTEM_PROMPT = "Ты — умный ассистент. Отвечай на вопросы пользователя, анализируя текст и изображения. Если на фото есть текст, можешь его прочитать, переписать или сократить по просьбе."

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы не засыпал) ---
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

# --- ОБРАБОТЧИК КОМАНДЫ /START ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Отправь мне текст или фотографию, и я помогу с ним разобраться. Если отправляешь фото, напиши в подписи, что нужно сделать.")

# --- ОБРАБОТЧИК ТЕКСТА ---
@dp.message(F.text)
async def handle_text(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.text}
            ],
            temperature=0.3, 
            max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка при обработке текста: {e}")
        await message.reply("Ошибка при обработке текста. Попробуй позже.")

# --- ОБРАБОТЧИК ФОТО (ИСПРАВЛЕН) ---
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Берем фото в максимальном качестве
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    
    # Переводим картинку в base64
    image_bytes = downloaded_file.read()
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    # Вопрос пользователя (подпись к фото, если есть, иначе стандартный)
    user_question = message.caption if message.caption else "Что на этой фотографии? Если есть текст, прочитай его."
    
    try:
        # Используем мультимодальную модель
        response = await client.chat.completions.create(
            model="meta-llama/llama-4-maverick-17b-128e-instruct",
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
        await message.reply("Не удалось обработать фото. Возможно, оно слишком большое или модель временно недоступна.")

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    print("Запускаем веб-сервер...")
    await start_web_server()
    
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
