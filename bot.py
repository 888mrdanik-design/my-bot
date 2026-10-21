import asyncio
import logging
import os
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import AsyncGroq
from aiohttp import web

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = ""
GROQ_API_KEY = ""

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))
                  
SYSTEM_PROMPT = (
    "Ты — профессиональный редактор. Сокращай и редактируй текст, который присылает пользователь. "
    "Сохраняй смысл, убирай воду, делай текст чётким и лаконичным. Отвечай только готовым текстом."
)
async def hangle(request):
  return web.Response(text="Bot is running!")
async def start_web_server():
  app = web.Application()
  app.router.add_get('/', handle)
  runner = web.AppRunner(app)
  await runner.setup()
  port = int(os.environ.get("PORT", 8080))
  site = web.TCPSite(runner, '0.0.0.0', port)
  await site.start()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Пришли мне текст, и я его сокращу и отредактирую.")

@dp.message()
async def handle_text(message: types.Message):
    user_text = message.text
    if not user_text:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.3,
            max_tokens=1500
        )
        
        result_text = response.choices[0].message.content
        await message.reply(result_text)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply("Ошибка при обработке текста. Попробуй позже.")
      
    async def main():
        logging.basicConfig(Level=logging.INFO)
        print("Запускаем веб-сервер для Render...")
        await start_web_server()

    print("Бот запущен (на Groq)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
