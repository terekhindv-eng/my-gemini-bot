import os
import io
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
import google.generativeai as genai
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# 1. Настройка разрешенной группы
try:
    ALLOWED_GROUP = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
except ValueError:
    ALLOWED_GROUP = 0

# 2. Белый список пользователей для личной переписки (Ваш ID)
ALLOWED_USERS = # Сюда автоматически подставится ваш рабочий ID из репозитория

bot = Bot(token=TOKEN)
dp = Dispatcher()
genai.configure(api_key=GEMINI_KEY)

# Стилистика общения Google AI + жесткое требование использовать HTML-теги для форматирования
GOOGLE_AI_SYSTEM_INSTRUCTION = (
    "Вы — официальный ИИ-ассистент Gemini от Google. Ваши ответы должны полностью "
    "соответствовать стилистике веб-интерфейса Google AI: будьте максимально полезным, "
    "конкретным, технологичным и точным. Избегайте пространных вступлений и дежурных фраз.\n\n"
    "ПРАВИЛО ФОРМАТИРОВАНИЯ: Тебе КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать символы звездочек (*) "
    "или нижних подчеркиваний (_) для выделения текста. Если тебе нужно сделать текст "
    "ЖИРНЫМ, используй строго теги <b>текст</b>. Если нужен КУРСИВ — используй <i>текст</i>. "
    "Для оформления списков используй стандартные маркеры (например, обычный дефис или точку) "
    "и перенос строки. Пишите в профессиональном, но дружелюбном тоне."
)

# Передаем системную инструкцию при создании модели
model = genai.GenerativeModel(
    "gemini-3.6-flash",
    system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION
)

# Локальное хранилище истории, разделенное по ID тем (топиков)
MAX_HISTORY = 40
chat_history = defaultdict(list)

# Глобальные переменные данных бота
BOT_USERNAME = ""
BOT_ID = 0

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if message.chat.type == "private" and message.from_user.id not in ALLOWED_USERS:
        await message.answer("❌ Общение с ботом в личных сообщениях запрещено. Бот работает только в рабочей группе.")
        return

    if message.chat.type in ["group", "supergroup"] and message.chat.id != ALLOWED_GROUP:
        try:
            await message.answer("❌ Этот бот приватный и не может работать в данной группе.")
            await bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return
        
    await message.answer("Привет! Я официальный ассистент Gemini. Чем могу помочь?")

# Расширенный хэндлер для обработки картинок, документов, аудио и голосовых сообщений
@dp.message(F.photo | F.document | F.audio | F.voice)
async def handle_files(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id

    if message.chat.type == "private" and message.from_user.id not in ALLOWED_USERS:
        await message.answer("❌ Общение с ботом в личных сообщениях запрещено. Бот работает только в рабочей группе.")
        return

    if message.chat.type in ["group", "supergroup"] and current_chat_id != ALLOWED_GROUP:
        try:
            await bot.leave_chat(current_chat_id)
        except Exception:
            pass
        return

    user_text = message.caption if message.caption else ""
    file_io = io.BytesIO()
    
    # Определяем ID текущей темы (если сообщение в основном чате/общем потоке, ID будет 0)
    thread_id = message.message_thread_id or 0
    
    if message.photo:
        file_info = message.photo[-1]
        mime_type = "image/jpeg"
        file_label = "[Фотография]"
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

    # Запись события в историю конкретной темы
    if message.chat.type in ["group", "supergroup"]:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {file_label} {user_text}")
        if len(chat_history[thread_id]) > MAX_HISTORY:
            chat_history[thread_id].pop(0)

    try:
        await bot.download(file_info, destination=file_io)
        file_bytes = file_io.getvalue()
    except Exception as e:
        await message.reply(f"❌ Не удалось загрузить файл: {str(e)}")
        return

    contents = [
        {
            "mime_type": mime_type,
            "data": file_bytes
        }
    ]

    # Сборка контекста из истории текущей темы
    if message.chat.type != "private" and chat_history[thread_id]:
        context = "\n".join(chat_history[thread_id])
        prompt_text = (
            f"Перед тобой история последних сообщений из этой темы рабочего чата:\n"
            f"\"\"\"\n{context}\n\"\"\"\n\n"
            f"Пользователь прикрепил медиафайл ({file_label}) и оставил запрос: {user_text}\n"
            f"Проанализируй прикрепленный файл, опираясь на контекст беседы текущей темы."
        )
    else:
        prompt_text = user_text if user_text else "Проанализируй содержимое этого медиафайла и детально опиши/расшифруй его."

    contents.append(prompt_text)
    await send_to_gemini(message, contents)


# Обработчик обычных текстовых сообщений
@dp.message()
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id

    if message.chat.type == "private" and message.from_user.id not in ALLOWED_USERS:
        await message.answer("❌ Общение с ботом в личных сообщениях запрещено. Бот работает только в рабочей группе.")
        return

    if message.chat.type in ["group", "supergroup"] and current_chat_id != ALLOWED_GROUP:
        try:
            await bot.leave_chat(current_chat_id)
        except Exception:
            pass
        return

    # Определяем ID текущей темы
    thread_id = message.message_thread_id or 0

    # Записываем текущее сообщение в историю конкретной темы
    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {message.text}")
        if len(chat_history[thread_id]) > MAX_HISTORY:
            chat_history[thread_id].pop(0)

    # Реакция на текст
    if (message.chat.type == "private" and message.from_user.id in ALLOWED_USERS) or current_chat_id == ALLOWED_GROUP:
        
        clean_request = message.text.replace(BOT_USERNAME, "").strip() if message.text else ""
        if not clean_request:
            clean_request = message.text

        # Передаем историю переписки только для групповых топиков
        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            full_prompt = (
                f"Перед тобой история последних сообщений из этой темы рабочего чата:\n"
                f"\"\"\"\n{context}\n\"\"\"\n\n"
                f"Выполни запрос пользователя, опираясь на эту историю чата: {clean_request}"
            )
        else:
            full_prompt = clean_request

        await send_to_gemini(message, [full_prompt])


# Единая функция отправки запросов в Gemini API с обработкой ошибок
async def send_to_gemini(message: types.Message, contents: list):
    for attempt in range(3):
        try:
            response = model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7
                )
            )
            
            if not response.text:
                await message.reply("🔄 Извините, не удалось сгенерировать ответ. Попробуйте еще раз.")
                return

            raw_text = response.text
            raw_text = raw_text.replace("**", "")

            # Отправляем ответ, используя безопасный режим HTML
            await message.reply(raw_text, parse_mode=ParseMode.HTML)
            return
            
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "quota" in err_msg.lower():
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                else:
                    await message.reply("⏳ Извините, сейчас слишком много запросов к ИИ. Подождите минуту и повторите.")
            else:
                await message.reply(f"Ошибка Gemini API: {err_msg}")
                return

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    global BOT_USERNAME, BOT_ID
    
    bot_info = await bot.get_me()
    BOT_USERNAME = f"@{bot_info.username}"
    BOT_ID = bot_info.id
    print(f"Бот {BOT_USERNAME} успешно запущен для группы ID: {ALLOWED_GROUP}!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    asyncio.create_task(site.start())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run=main() # Запуск polling-сервера