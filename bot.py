import asyncio
import logging
import os
import io
import base64
import re
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from groq import AsyncGroq
from aiohttp import web
from pptx import Presentation

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CHANNEL_ID = "@DDM_AI" # Не забудь вставить свой юзернейм!

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

# --- ФУНКЦИЯ ОЧИСТКИ ФОРМУЛ ОТ LATEX ---
def clean_math_text(text):
    if not text:
        return ""
    text = text.replace('$', '')
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'(\1)/(\2)', text)
    text = text.replace('\\cdot', '*')
    text = text.replace('\\cos', 'cos').replace('\\sin', 'sin').replace('\\tan', 'tan')
    text = text.replace('\\sqrt', 'sqrt')
    text = text.replace('\\left', '').replace('\\right', '')
    text = text.replace('\\circ', '°')
    text = text.replace('\\', '')
    return text

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

# --- ПРОВЕРКА ПОДПИСКИ ---
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Работа с текстом")
    builder.button(text="🤖 Задать вопрос")
    builder.button(text="✍️ Написать текст")
    builder.button(text="🎨 Фильтры для фото")
    builder.button(text="📊 Презентация")
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def get_subscribe_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}")
    builder.button(text="✅ Я подписался", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()

def get_filters_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Ч/Б", callback_data="filter_bw")
    builder.button(text="Размытие", callback_data="filter_blur")
    builder.button(text="Винтаж", callback_data="filter_vintage")
    builder.button(text="Контраст", callback_data="filter_contrast")
    builder.adjust(2)
    return builder.as_markup()

user_modes = {}

# --- КОМАНДА /START ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await is_subscribed(message.from_user.id):
        await message.answer(
            "Привет! Выбери режим.\n\n💡 *Совет:* Чтобы решить задачу по фото или задать вопрос по тексту, просто *ответь (Reply)* на нужное сообщение и напиши свой запрос!", 
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("Спасибо за подписку!", reply_markup=get_main_keyboard())
    else:
        await callback.answer("Вы еще не подписались!", show_alert=True)

# --- ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ ---
@dp.message(F.text == "📝 Работа с текстом")
async def mode_text(message: types.Message):
    user_modes[message.from_user.id] = "text"
    await message.answer("Режим редактора. Отправь текст.", reply_markup=get_main_keyboard())

@dp.message(F.text == "🤖 Задать вопрос")
async def mode_question(message: types.Message):
    user_modes[message.from_user.id] = "question"
    await message.answer("Режим вопросов. Спрашивай!", reply_markup=get_main_keyboard())

@dp.message(F.text == "✍️ Написать текст")
async def mode_write(message: types.Message):
    user_modes[message.from_user.id] = "write"
    await message.answer("Напиши мне тему и количество слов.\nНапример: *'Космос, 50 слов'*", reply_markup=get_main_keyboard())

@dp.message(F.text == "🎨 Фильтры для фото")
async def mode_filters(message: types.Message):
    user_modes[message.from_user.id] = "filter"
    await message.answer("Выбери фильтр:", reply_markup=get_filters_keyboard())

@dp.message(F.text == "📊 Презентация")
async def mode_presentation(message: types.Message):
    user_modes[message.from_user.id] = "presentation"
    await message.answer("Напиши тему презентации.", reply_markup=get_main_keyboard())

# --- ОБРАБОТКА ВЫБОРА ФИЛЬТРА ---
@dp.callback_query(F.data.startswith("filter_"))
async def process_filter_choice(callback: types.CallbackQuery):
    filter_type = callback.data.split("_")[1]
    user_modes[callback.from_user.id] = f"filter_{filter_type}"
    await callback.message.answer("Фильтр выбран! Отправь фото.")
    await callback.answer()

# =========================================================
# 🟢 ОБРАБОТКА ТЕКСТА И АНАЛИЗ ФОТО
# =========================================================
@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text in ["📝 Работа с текстом", "🤖 Задать вопрос", "✍️ Написать текст", "🎨 Фильтры для фото", "📊 Презентация"]:
        return

    if not await is_subscribed(message.from_user.id):
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # --- АНАЛИЗ ФОТО ПО REPLY (СО СЖАТИЕМ) ---
    if message.reply_to_message and message.reply_to_message.photo:
        await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
        photo = message.reply_to_message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        image = Image.open(io.BytesIO(downloaded_file.read()))
        image.thumbnail((1024, 1024))
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=85)
        output.seek(0)
        
        base64_image = base64.b64encode(output.read()).decode('utf-8')
        
        try:
            response = await client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[
                    {"role": "system", "content": "Ты — умный ассистент. Решай задачи по фото. Пиши подробное решение. ВАЖНО: не используй LaTeX и символы $ для формул. Пиши математику обычным текстом."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": message.text},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                temperature=0.3, max_tokens=1500
            )
            cleaned_response = clean_math_text(response.choices[0].message.content)
            await message.reply(cleaned_response)
        except Exception as e:
            logging.error(f"Ошибка анализа фото: {e}")
            await message.reply(f"Не удалось проанализировать фото: {e}")
        return

    mode = user_modes.get(message.from_user.id, "text")
    reply_to_text = message.reply_to_message.text if message.reply_to_message else ""

    # --- ПРЕЗЕНТАЦИЯ ---
    if mode == "presentation":
        await message.answer("Генерирую структуру и создаю файл...")
        sys_prompt = "Ты — эксперт по созданию презентаций. Формат: Слайд 1 | Заголовок | Текст\nСделай 5-7 слайдов."
        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": message.text}],
                temperature=0.5, max_tokens=2000
            )
            text_response = response.choices[0].message.content
            
            prs = Presentation()
            lines = text_response.strip().split('\n')
            for line in lines:
                if '|' in line:
                    parts = line.split('|')
                    if len(parts) >= 3:
                        slide_title = parts[1].strip()
                        slide_text = parts[2].strip()
                        slide_layout = prs.slide_layouts[1]
                        slide = prs.slides.add_slide(slide_layout)
                        title = slide.shapes.title
                        subtitle = slide.placeholders[1]
                        title.text = slide_title
                        subtitle.text = slide_text
            
            pptx_stream = io.BytesIO()
            prs.save(pptx_stream)
            pptx_stream.seek(0)
            document = BufferedInputFile(pptx_stream.read(), filename="presentation.pptx")
            await message.reply_document(document, caption="Твоя презентация готова! 🎉")
        except Exception as e:
            logging.error(f"Ошибка презентации: {e}")
            await message.reply(f"Не удалось создать презентацию: {e}")
        user_modes[message.from_user.id] = "text"
        return

    # --- НАПИСАНИЕ ТЕКСТА (С ВЫВОДОМ ОШИБОК) ---
    if mode == "write":
        prompt = message.text
        if reply_to_text: prompt = f"Контекст: {reply_to_text}\n\nЗадача: {message.text}"
        
        match = re.search(r'\d+', prompt)
        target_words = int(match.group()) if match else 0

        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": "Ты — талантливый писатель. Напиши текст на заданную тему. Пиши интересно и по делу."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7, max_tokens=1500
            )
            generated_text = response.choices[0].message.content
            
            if not generated_text or not generated_text.strip():
                logging.error("Нейросеть вернула пустой ответ.")
                await message.reply("Нейросеть не смогла написать текст. Попробуй переформулировать тему.")
                return

            if target_words > 0:
                words = generated_text.split()
                if len(words) > target_words:
                    generated_text = " ".join(words[:target_words]) + "..."
            
            final_word_count = len(generated_text.split())
            await message.reply(f"{generated_text}\n\n_(Слов: {final_word_count})_", parse_mode="Markdown")
            
        except Exception as e:
            logging.error(f"Ошибка написания: {e}")
            error_message = str(e).lower()
            if "rate limit" in error_message or "429" in error_message:
                await message.reply("Превышен лимит запросов к Groq. Подожди минуту и попробуй снова.")
            else:
                await message.reply(f"Ошибка при написании текста: {e}")
        return

    # --- ВОПРОСЫ / РЕДАКТОР ---
    if mode == "question":
        sys_prompt = "Ты — умный ассистент. Отвечай точно и понятно."
        temp = 0.5
    else:
        sys_prompt = "Ты — профессиональный редактор. Сокращай текст."
        temp = 0.3

    user_prompt = f"Контекст:\n\"{reply_to_text}\"\n\nВопрос:\n{message.text}" if reply_to_text else message.text

    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
            temperature=temp, max_tokens=1500
        )
        cleaned_response = clean_math_text(response.choices[0].message.content)
        await message.reply(cleaned_response)
    except Exception as e:
        logging.error(f"Ошибка обработки текста: {e}")
        await message.reply(f"Ошибка при обработке: {e}")
# =========================================================
# 🟢 КОНЕЦ ФУНКЦИИ handle_text
# =========================================================

# --- ОБРАБОТКА ФОТО (ФИЛЬТРЫ) ---
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if message.reply_to_message: return

    if not await is_subscribed(message.from_user.id):
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())
        return

    mode = user_modes.get(message.from_user.id, "")
    if not mode.startswith("filter_"):
        await message.reply("Выбери фильтр в меню, чтобы обработать фото.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    image = Image.open(io.BytesIO(downloaded_file.read()))
    
    filter_type = mode.split("_")[1]
    if filter_type == "bw": image = ImageOps.grayscale(image)
    elif filter_type == "blur": image = image.filter(ImageFilter.GaussianBlur(radius=5))
    elif filter_type == "vintage":
        image = ImageOps.grayscale(image)
        image = ImageOps.colorize(image, black="#2c1b18", white="#e0c9a6")
    elif filter_type == "contrast":
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)

    output = io.BytesIO()
    image.save(output, format='JPEG')
    output.seek(0)
    await message.reply_photo(photo=BufferedInputFile(output.read(), filename="result.jpg"), caption="Готово!")
    user_modes[message.from_user.id] = "text"

async def main():
    logging.basicConfig(level=logging.INFO)
    await start_web_server()
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
