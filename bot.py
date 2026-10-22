import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import AsyncGroq
from aiohttp import web  # Библиотека для веб-сервера

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = "Ты — профессиональный редактор. Сокращай и редактируй текст."

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080)) # Render сам подставит порт
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port}")

# --- ЛОГИКА БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Пришли мне текст, и я его сокращу.")

@dp.message()
async def handle_text(message: types.Message):
    user_text = message.text
    if not user_text:
        return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": user_text}],
            temperature=0.3,
            max_tokens=1500
        )
        await message.reply(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply("Ошибка при обработке. Попробуй позже.")

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Запускаем веб-сервер...")
    await start_web_server()
    
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
