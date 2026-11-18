import asyncio
import logging
import os
import io
import base64
import re
import json
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import BufferedInputFile
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from groq import AsyncGroq
from aiohttp import web
from pptx import Presentation
from pptx.util import Pt, Inches
from pptx.dml.color import RGBColor
from duckduckgo_search import DDGS # Библиотека для поиска картинок

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CHANNEL_ID = "@DDM_AI" # Не забудь вставить свой юзернейм!

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    exit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client = AsyncGroq(api_key=GROQ_API_KEY, http_client=httpx.AsyncClient(verify=False))

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

# --- СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await is_subscribed(message.from_user.id):
        await message.answer("Привет! Выбери режим.", reply_markup=get_main_keyboard())
    else:
        await message.answer("Подпишись на канал!", reply_markup=get_subscribe_keyboard())

@dp.callback_query(F.data == "check_sub")
async def check_subscription(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("Спасибо за подписку!", reply_markup=get_main_keyboard())
    else:
        await callback.answer("Вы еще не подписались!", show_alert=True)

# --- РЕЖИМЫ ---
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
    await message.answer("Напиши мне тему и количество слов.\nНапример: **'Космос, 50 слов'**", reply_markup=get_main_keyboard())

@dp.message(F.text == "🎨 Фильтры для фото")
async def mode_filters(message: types.Message):
    user_modes[message.from_user.id] = "filter"
    await message.answer("Выбери фильтр:", reply_markup=get_filters_keyboard())

@dp.message(F.text == "📊 Презентация")
async def mode_presentation(message: types.Message):
    user_modes[message.from_user.id] = "presentation"
    await message.answer("Напиши тему презентации, и я найду реальные фото из интернета для слайдов!", reply_markup=get_main_keyboard())

@dp.callback_query(F.data.startswith("filter_"))
async def process_filter_choice(callback: types.CallbackQuery):
    filter_type = callback.data.split("_")[1]
    user_modes[callback.from_user.id] = f"filter_{filter_type}"
    await callback.message.answer("Фильтр выбран! Отправь фото.")
    await callback.answer()

# =========================================================
# 🟢 ОБРАБОТКА ТЕКСТА
# =========================================================
@dp.message(F.text)
async def handle_text(message: types.Message):
    if message.text in ["📝 Работа с текстом", "🤖 Задать вопрос", "✍️ Написать текст", "🎨 Фильтры для фото", "📊 Презентация"]:
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
                    {"role": "system", "content": "Ты — умный ассистент. Решай задачи по фото. Пиши подробное решение."},
                    {"role": "user", "content": [{"type": "text", "text": message.text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
                ],
                temperature=0.3, max_tokens=1500
            )
            await message.reply(clean_math_text(response.choices[0].message.content))
        except Exception as e:
            await message.reply(f"Не удалось проанализировать фото: {e}")
        return

    mode = user_modes.get(message.from_user.id, "text")
    reply_to_text = message.reply_to_message.text if message.reply_to_message else ""

    # =========================================================
    # 🔥 РЕЖИМ ПРЕЗЕНТАЦИИ (ПОИСК РЕАЛЬНЫХ ФОТО)
    # =========================================================
    if mode == "presentation":
        await message.answer("Генерирую презентацию и ищу фото в интернете... Это займет около 30 секунд.")
        sys_prompt = (
            "Ты — профессиональный дизайнер презентаций. Создай структуру презентации на заданную тему. "
            "Верни ответ СТРОГО в формате JSON (без markdown-разметки). Формат:\n"
            "{\n"
            "  \"theme_color\": \"#1E3A8A\",\n"
            "  \"text_color\": \"#FFFFFF\",\n"
            "  \"slides\": [\n"
            "    {\n"
            "      \"title\": \"Заголовок слайда 1\", \n"
            "      \"text\": \"ОЧЕНЬ ПОДРОБНЫЙ ТЕКСТ (6-8 длинных предложений).\",\n"
            "      \"image_query\": \"Короткий запрос для поиска фото (на русском, например: 'Пушкин портрет' или 'город будущего')\",\n"
            "      \"use_background\": true\n"
            "    }\n"
            "  ]\n"
            "}\n"
            "Сделай 5 слайдов. Запросы для картинок (image_query) пиши на РУССКОМ языке, чтобы поиск нашел нужные фото."
        )
        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": message.text}],
                temperature=0.6, 
                max_tokens=4000
            )
            raw_response = response.choices[0].message.content.strip()
            
            if raw_response.startswith("```json"): raw_response = raw_response[7:-3]
            elif raw_response.startswith("```"): raw_response = raw_response[3:-3]
                
            data = json.loads(raw_response)
            
            prs = Presentation()
            theme_color = RGBColor.from_string(data.get("theme_color", "#1E3A8A").replace("#", ""))
            text_color = RGBColor.from_string(data.get("text_color", "#FFFFFF").replace("#", ""))
            
            # Функция для поиска и скачивания фото из интернета
            async def get_real_image(query):
                try:
                    # Ищем картинку через DuckDuckGo
                    results = DDGS().images(query, max_results=1, safesearch="moderate")
                    if results:
                        img_url = results[0]['image']
                        async with httpx.AsyncClient() as client_img:
                            resp = await client_img.get(img_url, timeout=15.0)
                            if resp.status_code == 200:
                                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                                # Затемняем для фона
                                enhancer = ImageEnhance.Brightness(img)
                                img = enhancer.enhance(0.4)
                                out = io.BytesIO()
                                img.save(out, format='JPEG')
                                return io.BytesIO(out.getvalue())
                except Exception as e:
                    logging.error(f"Ошибка поиска/скачивания фото: {e}")
                return None

            for slide_data in data.get("slides", []):
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                use_bg = slide_data.get("use_background", False)
                image_query = slide_data.get("image_query", "")
                
                # Фон
                if use_bg and image_query:
                    await message.answer(f"Ищу фото для: {slide_data.get('title')}...")
                    img_data = await get_real_image(image_query)
                    if img_data:
                        slide.shapes.add_picture(img_data, Inches(0), Inches(0), width=prs.slide_width, height=prs.slide_height)
                    else:
                        fill = slide.background.fill
                        fill.solid()
                        fill.fore_color.rgb = theme_color
                else:
                    fill = slide.background.fill
                    fill.solid()
                    fill.fore_color.rgb = theme_color
                
                # Заголовок
                title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(1))
                title_frame = title_box.text_frame
                title_frame.text = slide_data.get("title", "")
                p = title_frame.paragraphs[0]
                p.font.size = Pt(32)
                p.font.bold = True
                p.font.color.rgb = text_color
                p.font.name = 'Arial'
                
                # Подложка для текста
                text_bg = slide.shapes.add_shape(1, Inches(0.5), Inches(1.5), Inches(9), Inches(5.5))
                text_bg.fill.solid()
                text_bg.fill.fore_color.rgb = RGBColor(0, 0, 0)
                text_bg.fill.transparency = 0.6
                text_bg.line.fill.background()
                
                # Текст
                text_box = slide.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(8.8), Inches(5.3))
                text_frame = text_box.text_frame
                text_frame.text = slide_data.get("text", "")
                text_frame.word_wrap = True
                for paragraph in text_frame.paragraphs:
                    paragraph.font.size = Pt(14)
                    paragraph.font.color.rgb = text_color
                    paragraph.space_after = Pt(8)
                    paragraph.font.name = 'Arial'

            pptx_stream = io.BytesIO()
            prs.save(pptx_stream)
            pptx_stream.seek(0)
            document = BufferedInputFile(pptx_stream.read(), filename="presentation.pptx")
            await message.reply_document(document, caption="Твоя презентация с реальными фото готова! 🎉")
            
        except json.JSONDecodeError:
            await message.reply("Ошибка формата данных. Нейросеть запуталась. Попробуй еще раз.")
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
                messages=[{"role": "system", "content": "Ты — талантливый писатель. Напиши текст на заданную тему."}, {"role": "user", "content": prompt}],
                temperature=0.7, max_tokens=1500
            )
            generated_text = response.choices[0].message.content
            if not generated_text or not generated_text.strip(): raise ValueError("Пустой ответ")
            if target_words > 0:
                words = generated_text.split()
                if len(words) > target_words: generated_text = " ".join(words[:target_words]) + "..."
            await message.reply(f"{generated_text}\n\n_(Слов: {len(generated_text.split())})_", parse_mode="Markdown")
        except Exception as e:
            await message.reply(f"Ошибка: {e}")
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
