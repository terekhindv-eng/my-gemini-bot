import os
import io
import asyncio
import re
from collections import defaultdict, deque
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.utils.markdown import html_decoration as hd
from google import genai
from google.genai import types as genai_types
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Загрузка конфигурации из Environment Variables на Render
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))
# URL вашего приложения на Render (например, https://onrender.com). 
# Добавьте эту переменную в настройки (Environment Variables) на Render.com
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") 

ADMIN_ID = 490524856  # Ваш подтвержденный ID администратора для ЛС

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
    temperature=0.7
)

# Оптимизированное хранилище контекста (защита памяти от переполнения на Render)
MAX_HISTORY = 50
chat_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

BOT_USERNAME = ""
BOT_ID = 0

# Функция строгой проверки доступа к чатам и личке
def check_chat(message: types.Message) -> bool:
    # 1. Проверка личных сообщений (строго для ADMIN_ID)
    if message.chat.type == "private" and message.from_user.id != ADMIN_ID:
        return False
    
    # 2. Проверка группы (только для разрешенного TELEGRAM_GROUP_ID)
    try:
        allowed_group_id = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
    except ValueError:
        allowed_group_id = 0

    if message.chat.type in ["group", "supergroup"] and message.chat.id != allowed_group_id:
        return False
    return True

# Настройка при старте бота (получаем имя и ID бота автоматически)
@dp.startup()
async def on_startup(bot: Bot):
    global BOT_USERNAME, BOT_ID
    bot_user = await bot.get_me()
    BOT_USERNAME = bot_user.username
    BOT_ID = bot_user.id
    
    # Автоматически устанавливаем вебхук при запуске на Render
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
        await bot.set_webhook(webhook_url)

# 1. ХЭНДЛЕР /start
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if not check_chat(message):
        if message.chat.type in ["group", "supergroup"]:
            try: await bot.leave_chat(message.chat.id)
            except Exception: pass
        return
    await message.answer("Привет! Я официальный мультимодальный ассистент Gemini. Я умею анализировать текст, фото, видео, аудио файлы и помнить контекст беседы.")

# 2. МУЛЬТИМОДАЛЬНЫЙ ХЭНДЛЕР (Фото, видео, аудио, документы)
@dp.message(F.photo | F.video | F.document | F.audio | F.voice)
async def handle_files(message: types.Message):
    if not check_chat(message): 
        return

    user_text = message.caption if message.caption else ""
    file_io = io.BytesIO()
    thread_id = message.message_thread_id or 0
    
    if message.photo:
        file_info = message.photo[-1]
        mime_type = "image/jpeg"
        file_label = "[Фотография]"
    elif message.video:
        file_info = message.video
        mime_type = message.video.mime_type or "video/mp4"
        file_label = "[Видеофайл]"
    elif message.voice:
        file_info = message.voice
        mime_type = "audio/ogg"
        file_label = "[Голосовое сообщение]"
    elif message.audio:
        file_info = message.audio
        mime_type = message.audio.mime_type or "audio/mp3"
        file_label = "[Аудиофайл]"
    else:
        file_info = message.document
        mime_type = message.document.mime_type or "application/octet-stream"
        file_label = "[Документ]"

    # Сохраняем сообщение в историю для поддержания контекста темы
    if message.chat.type in ["group", "supergroup"]:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {file_label} {user_text}")

    # Бот реагирует на любое сообщение в разрешенном чате
    is_triggered = True

    if is_triggered:
        try:
            await bot.download(file_info, destination=file_io)
            file_bytes = file_io.getvalue()
        except Exception as e:
            await message.reply(f"❌ Не удалось загрузить медиафайл: {str(e)}")
            return

        file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            prompt_text = f"История последних сообщений в этой теме чата:\n{context}\n\nЗапрос к прикрепленному файлу: {user_text}"
        else:
            prompt_text = user_text if user_text else "Проанализируй содержимое этого медиафайла."

        await send_to_gemini(message, [file_part, prompt_text])

# 3. ТЕКСТОВЫЙ ХЭНДЛЕР
@dp.message(F.text)
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    if not check_chat(message): 
        return

    thread_id = message.message_thread_id or 0

    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {message.text}")

    # Бот реагирует на любое сообщение в разрешенном чате
    is_triggered = True

    if is_triggered:
        clean_request = message.text
        # Очищаем текст от имени бота, если оно было случайно указано
        if message.text and BOT_USERNAME.lower() in message.text.lower():
            clean_request = re.sub(re.escape(BOT_USERNAME), "", message.text, flags=re.IGNORECASE).strip()
        if "my_support_gemini_bot" in clean_request.lower():
            clean_request = re.sub("my_support_gemini_bot", "", clean_request, flags=re.IGNORECASE).strip()

        if not clean_request:
            clean_request = message.text

        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            full_prompt = f"История последних {MAX_HISTORY} сообщений в этой теме чата:\n{context}\n\nВыполни запрос пользователя: {clean_request}"
        else:
            full_prompt = clean_request

        await send_to_gemini(message, [full_prompt])

# Функция отправки запросов в Google GenAI API с моделью gemini
async def send_to_gemini(message: types.Message, contents: list):
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        
        # Название вашей ИИ модели (оно не менялось)
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=contents,
            config=TEXT_CONFIG
        )
        
        if response.text:
            await message.reply(response.text, parse_mode=ParseMode.HTML)
        else:
            await message.reply("⚠️ Бот вернул пустой ответ.")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка при обращении к Gemini API: {str(e)}")

# --- ФИНАЛЬНАЯ ЧАСТЬ: ЗАПУСК ВЕБ-СЕРВЕРА ДЛЯ RENDER.COM ---
def main():
    app = web.Application()
    
    # Настраиваем обработчик входящих уведомлений от Telegram по адресу /webhook
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot
    )
    webhook_requests_handler.register(app, path="/webhook")
    
    # Связываем aiogram и aiohttp приложение
    setup_application(app, dp, bot=bot)
    
    # Запускаем сервер на порту, который выделил Render
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
