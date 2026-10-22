import asyncio
import logging
import os
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from groq import AsyncGroq

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.environ.get
GROQ_API_KEY = os.environ.get

logging.basikConfig(level=logging.INFO)
if TELEGRAM_TOKEN:
  logging.info(f"Токен получен , начинается с:
  {TELEGRAM_TOKEN[:10]}...")
else:
    logging.error("КРИТИЧЕСКАЯ ОШИБКА: TELEGRAM_TOKEN ПУСТОЙ!")
if GROQ_API_KEY:
    logging.info(f"Ключ Groq получен, начинается с: {GROQ_API_KEY[:10]}...")
else:
  logging.error("КРИТИЧЕСКАЯ ОШИБКА: GROQ_API_KEY ПУСТОЙ!")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
  exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))
                  
SYSTEM_PROMPT = (
    "Ты — профессиональный редактор. Сокращай и редактируй текст, который присылает пользователь. "
    "Сохраняй смысл, убирай воду, делай текст чётким и лаконичным. Отвечай только готовым текстом."
)

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
      await
        result_text = response.choices[0].message.content
)

    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.reply("Ошибка при обработке текста. Попробуй позже.")
      
    async def main():
        logging.basicConfig(Level=logging.INFO)
        print("Запускаем веб-сервер для Render...")
        await start_web_server()

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
