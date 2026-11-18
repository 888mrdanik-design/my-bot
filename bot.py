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

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CHANNEL_ID = "@DDM_AI" # Не забудь вставить свой юзернейм!
ADMIN_ID = 5454075897 # ВСТАВЬ СЮДА СВОЙ ЧИСЛОВОЙ ID!

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

is_maintenance = False

# =========================================================
# 🛡️ МИДДЛВАРЬ ДЛЯ ТЕХ. РАБОТ
# =========================================================
class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data):
        if is_maintenance and event.from_user.id != ADMIN_ID:
            if event.text != "⚙️ Админ-панель":
                await event.answer("🛠 Ведутся технические работы. Пожалуйста, зайдите позже.")
                return
        return await handler(event, data)

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
        await message.answer("👋 Привет! Выбери режим кнопками внизу 👇", reply_markup=get_main_keyboard(message.from_user.id))
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
                messages=[{"role": "system", "content": "Ты — умный ассистент. Решай задачи по фото."},{"role": "user", "content": [{"type": "text", "text": message.text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}],
                temperature=0.3, max_tokens=1500
            )
            await message.reply(clean_math_text(response.choices[0].message.content))
        except Exception as e:
            await message.reply(f"Ошибка фото: {e}")
        return

    mode = user_modes.get(message.from_user.id, "text")
    reply_to_text = message.reply_to_message.text if message.reply_to_message else ""

    # --- ПРЕЗЕНТАЦИЯ С КАРТИНКАМИ ---
    if mode == "presentation":
        await message.answer("Анализирую тему и готовлю презентацию с фото...")
        sys_prompt = (
            "Ты — профессиональный дизайнер презентаций. Создай структуру презентации на заданную тему. "
            "ОТВЕЧАЙ СТРОГО НА РУССКОМ ЯЗЫКЕ! "
            "Верни ответ СТРОГО в формате JSON (без markdown-разметки). Формат:\n"
            "{\n"
            "  \"theme_color\": \"#1E3A8A\",\n"
            "  \"text_color\": \"#FFFFFF\",\n"
            "  \"slides\": [\n"
            "    {\n"
            "      \"title\": \"Заголовок слайда 1\", \n"
            "      \"text\": \"ОЧЕНЬ ПОДРОБНЫЙ ТЕКСТ НА РУССКОМ ЯЗЫКЕ (6-8 длинных предложений).\",\n"
            "      \"image_query\": \"ОБЯЗАТЕЛЬНОЕ ПОЛЕ! Запрос для поиска в Википедии на русском (например: 'Пушкин Александр Сергеевич')\"\n"
            "    }\n"
            "  ]\n"
            "}\n"
            "Сделай 5 слайдов."
        )
        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": message.text}],
                temperature=0.6, max_tokens=4000
            )
            raw_response = response.choices[0].message.content.strip()
            
            if raw_response.startswith("```json"): raw_response = raw_response[7:-3]
            elif raw_response.startswith("```"): raw_response = raw_response[3:-3]
                
            data = json.loads(raw_response)
            
            prs = Presentation()
            theme_color = RGBColor.from_string(data.get("theme_color", "#1E3A8A").replace("#", ""))
            text_color = RGBColor.from_string(data.get("text_color", "#FFFFFF").replace("#", ""))
            
            # Функция поиска картинки (сначала Википедия, потом генерация)
            async def get_image_for_slide(query):
                # 1. Пробуем Википедию
                try:
                    search_url = f"https://ru.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch={quote(query)}&gsrlimit=1&prop=pageimages&piprop=original&format=json"
                    async with httpx.AsyncClient() as client_img:
                        resp = await client_img.get(search_url, timeout=10.0)
                        if resp.status_code == 200:
                            json_data = resp.json()
                            pages = json_data.get("query", {}).get("pages", {})
                            for page_id, page_data in pages.items():
                                if "original" in page_data:
                                    img_url = page_data["original"]["source"]
                                    img_resp = await client_img.get(img_url, timeout=10.0)
                                    if img_resp.status_code == 200:
                                        img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
                                        out = io.BytesIO()
                                        img.save(out, format='JPEG')
                                        return io.BytesIO(out.getvalue())
                except Exception:
                    pass
                
                # 2. Если Википедия не дала фото, генерируем через Pollinations
                try:
                    gen_url = f"https://image.pollinations.ai/prompt/{quote(query)}?width=800&height=600&nologo=true"
                    async with httpx.AsyncClient() as client_img:
                        resp = await client_img.get(gen_url, timeout=20.0)
                        if resp.status_code == 200:
                            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                            out = io.BytesIO()
                            img.save(out, format='JPEG')
                            return io.BytesIO(out.getvalue())
                except Exception:
                    pass
                
                return None

            for i, slide_data in enumerate(data.get("slides", [])):
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                
                fill = slide.background.fill
                fill.solid()
                fill.fore_color.rgb = theme_color

                image_query = slide_data.get("image_query", "")
                if image_query:
                    await message.answer(f"({i+1}/5) Ищу/генерирую фото: {slide_data.get('title')}...")
                    img_data = await get_image_for_slide(image_query)
                    if img_data:
                        slide.shapes.add_picture(img_data, Inches(5.5), Inches(1.5), width=Inches(4.0))
                
                await asyncio.sleep(1.0)

                title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(1))
                title_frame = title_box.text_frame
                title_frame.text = slide_data.get("title", "")
                p = title_frame.paragraphs[0]
                p.font.size = Pt(32)
                p.font.bold = True
                p.font.color.rgb = RGBColor(255, 255, 255)
                p.font.name = 'Arial'
                
                text_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(4.8), Inches(5.5))
                text_frame = text_box.text_frame
                text_frame.text = slide_data.get("text", "")
                text_frame.word_wrap = True
                for paragraph in text_frame.paragraphs:
                    paragraph.font.size = Pt(14)
                    paragraph.font.color.rgb = RGBColor(255, 255, 255)
                    paragraph.space_after = Pt(8)
                    paragraph.font.name = 'Arial'

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

    # --- НАПИСАНИЕ ТЕКСТА ---
    if mode == "write":
        prompt = message.text
        if reply_to_text: prompt = f"Контекст: {reply_to_text}\n\nЗадача: {message.text}"
        
        match = re.search(r'\d+', prompt)
        target_words = int(match.group()) if match else 0

        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": "Ты — талантливый писатель. Напиши интересный и подробный текст на заданную тему. ОТВЕЧАЙ СТРОГО НА РУССКОМ ЯЗЫКЕ. Не пиши слишком коротко."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7, 
                max_tokens=1500
            )
            generated_text = response.choices[0].message.content
            
            if not generated_text or not generated_text.strip():
                logging.error("Нейросеть вернула пустой ответ.")
                await message.reply("Нейросеть не смогла написать текст. Попробуй еще раз.")
                return

            if target_words > 0:
                words = generated_text.split()
                if len(words) > target_words:
                    generated_text = " ".join(words[:target_words]) + "..."
            
            final_word_count = len(generated_text.split())
            await message.reply(f"{generated_text}\n\n_(Слов: {final_word_count})_", parse_mode="Markdown")
            
        except Exception as e:
            logging.error(f"Ошибка написания: {e}")
            await message.reply(f"Ошибка при написании: {e}")
        return

    # --- ВОПРОСЫ / РЕДАКТОР ---
    sys_prompt = "Ты — умный ассистент. Отвечай на русском языке." if mode == "question" else "Ты — профессиональный редактор. Сокращай текст на русском языке."
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

# --- ОБРАБОТКА ФОТО (ФИЛЬТРЫ) ---
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
