import asyncio
import logging
import os
import io
import base64
import re
import json
import httpx
from urllib.parse import quote
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from groq import AsyncGroq
from aiohttp import web
from pptx import Presentation
from pptx.util import Pt, Inches
from pptx.dml.color import RGBColor

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CHANNEL_ID = "@DDM_AI" # Не забудь вставить свой юзернейм!
ADMIN_ID = 5454075897 # ВСТАВЬ СЮДА СВОЙ ЧИСЛОВОЙ ID!

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

# Глобальный флаг для режима обслуживания
is_maintenance = False

# =========================================================
# 🛡️ МИДДЛВАРЬ ДЛЯ БЛОКИРОВКИ ВСЕХ ДЕЙСТВИЙ ВО ВРЕМЯ ТЕХ. РАБОТ
# =========================================================
class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data):
        # Если тех. работы включены, и пишет не админ
        if is_maintenance and event.from_user.id != ADMIN_ID:
            # Проверяем, не нажал ли он админку (на всякий случай)
            if event.text != "⚙️ Админ-панель":
                await event.answer("🛠 Ведутся технические работы. Пожалуйста, зайдите позже.")
                return # Блокируем дальнейшую обработку
        # Если тех. работы выключены или это админ — пропускаем дальше
        return await handler(event, data)

# Подключаем миддлварь ко всем сообщениям
dp.message.middleware(MaintenanceMiddleware())

# --- ОЧИСТКА ОТ LATEX ---
def clean_math_text(text):
    if not text: return ""
    text = text.replace('$', '')
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'(\1)/(\2)', text)
    text = text.replace('\\cdot', '*').replace('\\cos', 'cos').replace('\\sin', 'sin')
    text = text.replace('\\left', '').replace('\\right', '').replace('\\circ', '°')
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
def get_main_keyboard(user_id):
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Работа с текстом")
    builder.button(text="🤖 Задать вопрос")
    builder.button(text="✍️ Написать текст")
    builder.button(text="🎨 Фильтры для фото")
    builder.button(text="📊 Презентация")
    if user_id == ADMIN_ID:
        builder.button(text="⚙️ Админ-панель")
        builder.adjust(2, 2, 2)
    else:
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

def get_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔴 Включить тех. работы" if not is_maintenance else "🟢 Выключить тех. работы", callback_data="toggle_maintenance")
    builder.adjust(1)
    return builder.as_markup()

user_modes = {}

# --- СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await is_subscribed(message.from_user.id):
        await message.answer(
            "👋 Привет! Выбери режим кнопками внизу 👇",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
    else:
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("Спасибо за подписку!", reply_markup=get_main_keyboard(callback.from_user.id))
    else:
        await callback.answer("Вы еще не подписались!", show_alert=True)

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    status = "ВКЛЮЧЕНЫ" if is_maintenance else "ВЫКЛЮЧЕНЫ"
    await message.answer(f"Тех. работы: **{status}**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "toggle_maintenance")
async def toggle_maintenance(callback: types.CallbackQuery):
    global is_maintenance
    if callback.from_user.id != ADMIN_ID: return
    is_maintenance = not is_maintenance
    status = "ВКЛЮЧЕНЫ" if is_maintenance else "ВЫКЛЮЧЕНЫ"
    await callback.message.edit_text(f"Тех. работы: **{status}**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

# --- РЕЖИМЫ ---
@dp.message(F.text == "📝 Работа с текстом")
async def mode_text(message: types.Message):
    user_modes[message.from_user.id] = "text"
    await message.answer("Отправь текст.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "🤖 Задать вопрос")
async def mode_question(message: types.Message):
    user_modes[message.from_user.id] = "question"
    await message.answer("Спрашивай!", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "✍️ Написать текст")
async def mode_write(message: types.Message):
    user_modes[message.from_user.id] = "write"
    await message.answer("Напиши тему и количество слов.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "🎨 Фильтры для фото")
async def mode_filters(message: types.Message):
    user_modes[message.from_user.id] = "filter"
    await message.answer("Выбери фильтр:", reply_markup=get_filters_keyboard())

@dp.message(F.text == "📊 Презентация")
async def mode_presentation(message: types.Message):
    user_modes[message.from_user.id] = "presentation"
    await message.answer("Напиши тему презентации.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.callback_query(F.data.startswith("filter_"))
async def process_filter_choice(callback: types.CallbackQuery):
    user_modes[callback.from_user.id] = f"filter_{callback.data.split('_')[1]}"
    await callback.message.answer("Фильтр выбран! Отправь фото.")
    await callback.answer()

# =========================================================
# 🟢 ОБРАБОТКА ТЕКСТА
# =========================================================
@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text in ["📝 Работа с текстом", "🤖 Задать вопрос", "✍️ Написать текст", "🎨 Фильтры для фото", "📊 Презентация", "⚙️ Админ-панель"]:
        return

    if not await is_subscribed(message.from_user.id):
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # --- АНАЛИЗ ФОТО ---
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
                    {"role": "system", "content": "Ты — умный ассистент. Решай задачи по фото."},
                    {"role": "user", "content": [{"type": "text", "text": message.text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
                ],
                temperature=0.3, max_tokens=1500
            )
            await message.reply(clean_math_text(response.choices[0].message.content))
        except Exception as e:
            await message.reply(f"Ошибка фото: {e}")
        return

    mode = user_modes.get(message.from_user.id, "text")
    reply_to_text = message.reply_to_message.text if message.reply_to_message else ""

    # --- ПРЕЗЕНТАЦИЯ ---
    if mode == "presentation":
        # (Вставь сюда свой большой код презентации)
        await message.answer("Генерирую презентацию...")
        return

    # --- НАПИСАНИЕ ТЕКСТА ---
    if mode == "write":
        # (Вставь сюда код написания текста)
        await message.answer("Генерирую текст...")
        return

    # --- ВОПРОСЫ / РЕДАКТОР ---
    sys_prompt = "Ты — умный ассистент." if mode == "question" else "Ты — профессиональный редактор. Сокращай текст."
    temp = 0.5 if mode == "question" else 0.3
    user_prompt = f"Контекст:\n\"{reply_to_text}\"\n\nВопрос:\n{message.text}" if reply_to_text else message.text
    try:
        response = await client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
            temperature=temp, max_tokens=1500
        )
        await message.reply(clean_math_text(response.choices[0].message.content))
    except Exception as e:
        await message.reply(f"Ошибка: {e}")

# --- ОБРАБОТКА ФОТО ---
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if message.reply_to_message: return
    if not await is_subscribed(message.from_user.id): return
    
    mode = user_modes.get(message.from_user.id, "")
    if not mode.startswith("filter_"): return

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
