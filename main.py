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

# Загрузка переменных окружения из настроек Render
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = 490524856  # Ваш личный Telegram ID

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
    # 1. Проверка личных сообщений (только для вашего ADMIN_ID)
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

# 1. ХЭНДЛЕР /start
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if not check_chat(message):
        if message.chat.type in ["group", "supergroup"]:
            try: 
                await bot.leave_chat(message.chat.id)
            except Exception: 
                pass
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

    # Сохраняем факт отправки файла в историю для контекста группы
    if message.chat.type in ["group", "supergroup"]:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {file_label} {user_text}")

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

    # Записываем все сообщения группы в историю (для памяти контекста)
    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {message.text}")

    # Бот отвечает в ЛС всегда, а в группе — только на упоминание или ответ на его сообщение
    is_triggered = (
        message.chat.type == "private" or 
        (message.text and BOT_USERNAME.lower() in message.text.lower()) or 
        (message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID)
    )

    if is_triggered:
        clean_request = message.text
        # Удаляем юзернейм бота из текста запроса (без учета регистра)
        if message.text and BOT_USERNAME.lower() in message.text.lower():
            clean_request = re.sub(re.escape(BOT_USERNAME), "", message.text, flags=re.IGNORECASE).strip()

        if not clean_request:
            clean_request = message.text

        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            full_prompt = f"История последних {MAX_HISTORY} сообщений в этой теме чата:\n{context}\n\nВыполни запрос пользователя: {clean_request}"
        else:
            full_prompt = clean_request

        await send_to_gemini(message, [full_prompt])

# Функция отправки запросов в Google GenAI API с актуальной моделью 3.6
async def send_to_gemini(message: types.Message, contents: list):
    try:
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',  # Актуальная рабочая модель
            contents=contents,
            config=TEXT_CONFIG
        )
        if not response.text:
            await message.reply("🔄 Не удалось получить ответ от модели. Попробуйте снова.")
            return
        
        # Предварительная очистка от Markdown-звездочек
        raw_text = response.text.replace("**", "").replace("* ", "- ")
        
        # Безопасная отправка HTML: если разметка сломана ИИ, шлем как чистый текст
        try:
            await message.reply(raw_text, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply(hd.quote(raw_text), parse_mode=ParseMode.HTML)
            
    except Exception as e:
        await message.reply(f"Ошибка Gemini API: {str(e)}")

# Веб-интерфейс для прохождения проверок портов Render (и пинга от cron-job)
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    global BOT_USERNAME, BOT_ID
    bot_info = await bot.get_me()
    BOT_USERNAME = f"@{bot_info.username}"
    BOT_ID = bot_info.id
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск веб-сервера aiohttp в фоне, чтобы он не мешал циклу Telegram
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    print(f"Бот {BOT_USERNAME} успешно запущен на порту {PORT}!")
    
    # Запуск Long Polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
