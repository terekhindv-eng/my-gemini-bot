import os
import io
import asyncio
import re
import logging
from collections import defaultdict, deque
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.markdown import html_decoration as hd
from google import genai
from google.genai import types as genai_types
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Включаем логирование, чтобы видеть состояние памяти в консоли Render
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") 

ADMIN_ID = 490524856  

bot = Bot(token=TOKEN)
dp = Dispatcher()
ai_client = genai.Client(api_key=GEMINI_KEY)

GOOGLE_AI_SYSTEM_INSTRUCTION = (
    "Вы — официальный ИИ-ассистент Gemini от Google. Ваши ответы должны полностью "
    "соответствовать стилитике веб-интерфейса Google AI: будьте максимально полезным, "
    "конкретным, технологичным и точным. Избегайте пространных вступлений и дежурных фраз.\n\n"
    "ПРАВИЛО ФОРМАТИРОВАНИЯ: Тебе КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать символы звездочек (*) "
    "или нижних подчеркиваний (_) для выделения текста. Если тебе нужно сделать текст "
    "ЖИРНЫМ, используй строго теги <b>текст</b>. Если нужен КУРСИВ — используй <i>текст</i>. "
    "Для оформления списков используй стандартные маркеры (например, обычный дефис или точку) "
    "и перенос строки. Пишите в профессиональном, но дружелюбном тоне."
)

TEXT_CONFIG = genai_types.GenerateContentConfig(
    system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION,
    max_output_tokens=1500  
)

# Храним историю до 50 сообщений. Ключ — это кортеж (chat_id, thread_id) для абсолютной точности
MAX_HISTORY = 50
chat_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

BOT_USERNAME = ""
BOT_ID = 0

def check_chat(message: types.Message) -> bool:
    if message.chat.type == "private" and message.from_user.id != ADMIN_ID:
        return False
    try:
        allowed_group_id = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
    except ValueError:
        allowed_group_id = 0
    if message.chat.type in ["group", "supergroup"] and message.chat.id != allowed_group_id:
        return False
    return True

# Получение уникального ключа для словаря истории
def get_history_key(message: types.Message):
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    return (chat_id, thread_id)

@dp.startup()
async def on_startup(bot: Bot):
    global BOT_USERNAME, BOT_ID
    bot_user = await bot.get_me()
    BOT_USERNAME = bot_user.username
    BOT_ID = bot_user.id
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
        await bot.set_webhook(webhook_url)
    logging.info(f"Бот @{BOT_USERNAME} успешно запущен!")

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if not check_chat(message):
        if message.chat.type in ["group", "supergroup"]:
            try: await bot.leave_chat(message.chat.id)
            except Exception: pass
        return
    key = get_history_key(message)
    chat_history[key].clear()
    await message.answer("Привет! Контекст нашей беседы полностью очищен. Я готов к общению и буду помнить до 50 сообщений!")

# 2. МУЛЬТИМОДАЛЬНЫЙ ХЭНДЛЕР
@dp.message(F.photo | F.video | F.document | F.audio | F.voice)
async def handle_files(message: types.Message):
    if not check_chat(message): 
        return

    user_text = message.caption if message.caption else ""
    file_io = io.BytesIO()
    key = get_history_key(message)
    
    if message.photo:
        file_info = message.photo[-1]
        mime_type = "image/jpeg"
        file_label = "[Фото]"
    elif message.video:
        file_info = message.video
        mime_type = message.video.mime_type or "video/mp4"
        file_label = "[Видео]"
    elif message.voice:
        file_info = message.voice
        mime_type = "audio/ogg"
        file_label = "[Голосовое]"
    elif message.audio:
        file_info = message.audio
        mime_type = message.audio.mime_type or "audio/mp3"
        file_label = "[Аудио]"
    else:
        file_info = message.document
        mime_type = message.document.mime_type or "application/octet-stream"
        file_label = "[Документ]"

    try:
        await bot.download(file_info, destination=file_io)
        file_bytes = file_io.getvalue()
    except Exception as e:
        await message.reply(f"❌ Не удалось загрузить медиафайл: {str(e)}")
        return

    file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    text_part = genai_types.Part.from_text(text=user_text if user_text else f"Проанализируй этот файл {file_label}.")
    user_content = genai_types.Content(role="user", parts=[file_part, text_part])

    history_text = f"{file_label}: {user_text}".strip()
    history_user_content = genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=history_text)])

    await send_to_gemini(message, user_content, history_user_content, key)

# 3. ТЕКСТОВЫЙ ХЭНДЛЕР
@dp.message(F.text)
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    if not check_chat(message): 
        return

    key = get_history_key(message)
    clean_request = message.text

    if BOT_USERNAME.lower() in clean_request.lower():
        clean_request = re.sub(re.escape(BOT_USERNAME), "", clean_request, flags=re.IGNORECASE).strip()
    if "my_support_gemini_bot" in clean_request.lower():
        clean_request = re.sub("my_support_gemini_bot", "", clean_request, flags=re.IGNORECASE).strip()

    if not clean_request:
        clean_request = message.text

    user_content = genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=clean_request)])
    await send_to_gemini(message, user_content, user_content, key)

# Внутренняя фоновая асинхронная задача
async def _background_gemini_task(message: types.Message, current_user_content: genai_types.Content, history_user_content: genai_types.Content, key: tuple):
    # Загружаем накопленную историю для этого конкретного чата
    history_list = list(chat_history[key])
    full_contents = history_list + [current_user_content]
    
    logging.info(f"Чат {key}: отправка запроса. Сообщений в истории до этого: {len(history_list)}")
    
    response = None
    max_retries = 4
    delay = 2

    for attempt in range(max_retries):
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            response = await ai_client.aio.models.generate_content(
                model="gemini-3.1-flash-lite", 
                contents=full_contents,
                config=TEXT_CONFIG
            )
            break
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg:
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
            try:
                await message.reply(f"❌ Ошибка при обращении к Gemini API: {err_msg}")
            except Exception: pass
            return

    if response and response.text:
        text = response.text
        
        # Сохраняем шаг диалога в память
        chat_history[key].append(history_user_content)
        ai_content = genai_types.Content(role="model", parts=[genai_types.Part.from_text(text=text)])
        chat_history[key].append(ai_content)
        
        logging.info(f"Чат {key}: ответ успешно добавлен. Размер истории теперь: {len(chat_history[key])}")

        if len(text) <= 4000:
            try:
                await message.reply(text, parse_mode=ParseMode.HTML)
            except Exception:
                await message.reply(hd.quote(text), parse_mode=None)
        else:
            chunks = []
            while len(text) > 4000:
                split_idx = text.rfind('\n', 0, 4000)
                if split_idx == -1 or split_idx < 3000:
                    split_idx = 4000
                chunks.append(text[:split_idx])
                text = text[split_idx:]
            chunks.append(text)
            
            for chunk in chunks:
                if chunk.strip():
                    try:
                        await message.reply(chunk, parse_mode=ParseMode.HTML)
                    except Exception:
                        await message.reply(hd.quote(chunk), parse_mode=None)
                    await asyncio.sleep(1.0)
    else:
        try:
            await message.reply("⚠️ Бот вернул пустой ответ.")
        except Exception: pass

async def send_to_gemini(message: types.Message, current_user_content: genai_types.Content, history_user_content: genai_types.Content, key: tuple):
    asyncio.create_task(_background_gemini_task(message, current_user_content, history_user_content, key))

async def health_check(request):
    return web.Response(text="Бот активен", status=200)

def main():
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path="/webhook")
    app.router.add_get("/", health_check)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)

main()
