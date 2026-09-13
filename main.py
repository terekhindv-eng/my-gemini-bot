import os
import io
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from google import genai
from google.genai import types as genai_types
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# 1. Настройка разрешенной группы
try:
    ALLOWED_GROUP = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
except ValueError:
    ALLOWED_GROUP = 0

# 2. Белый список пользователей для личной переписки
ALLOWED_USERS = [490524856]  # ОБЯЗАТЕЛЬНО вставьте ваш числовой Telegram ID внутрь скобок!

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Инициализируем новый официальный клиент Gemini
ai_client = genai.Client(api_key=GEMINI_KEY)

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

# Настройка конфигурации для текстовой модели
TEXT_CONFIG = genai_types.GenerateContentConfig(
    system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION,
    temperature=0.7
)

# Локальное хранилище истории, разделенное по ID тем (топиков). Установлен лимит в 50 сообщений.
MAX_HISTORY = 50
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


# Хэндлер для генерации изображений по команде /draw или /рендери
@dp.message(Command("draw", "рендери"))
async def generate_image_cmd(message: types.Message):
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

    image_prompt = message.text.split(maxsplit=1).strip() if len(message.text.split()) > 1 else ""

    if not image_prompt:
        await message.reply("❌ <b>Вы не ввели описание для картинки!</b>\nПример использования:\n<code>/draw милый рыжий кот в очках космического скафандра</code>", parse_mode=ParseMode.HTML)
        return

    status_msg = await message.reply("🎨 <i>Генерирую изображение по вашему запросу, пожалуйста, подождите...</i>", parse_mode=ParseMode.HTML)

    for attempt in range(3):
        try:
            # Генерация картинок через новый клиент на модели Imagen 3
            result = ai_client.models.generate_images(
                model='imagen-3.0-generate-002',
                prompt=image_prompt,
                config=genai_types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/jpeg",
                    aspect_ratio="1:1"
                )
            )

            image_bytes = None
            if result.generated_images:
                image_bytes = result.generated_images.image.image_bytes

            if not image_bytes:
                await status_msg.edit_text("🔄 Извините, не удалось извлечь изображение из ответа ИИ. Попробуйте изменить формулировку промпта.")
                return

            input_file = types.BufferedInputFile(image_bytes, filename="generated_image.jpg")
            await bot.delete_message(chat_id=current_chat_id, message_id=status_msg.message_id)
            await message.reply_photo(photo=input_file, caption=f"✨ Готово! Изображение по запросу: <i>{image_prompt}</i>", parse_mode=ParseMode.HTML)
            return

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "quota" in err_msg.lower():
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                else:
                    await status_msg.edit_text("⏳ Сейчас слишком много запросов к генератору картинок. Подождите минуту.")
            else:
                await status_msg.edit_text(f"❌ Ошибка генерации Imagen API: {err_msg}")
                return


# Расширенный хэндлер для обработки входящих картинок, документов, аудио и голосовых
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

    # Структурируем файл под новый формат данных google-genai
    file_part = genai_types.Part.from_bytes(
        data=file_bytes,
        mime_type=mime_type,
    )

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

    await send_to_gemini(message, [file_part, prompt_text])


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

    thread_id = message.message_thread_id or 0

    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {message.text}")
        if len(chat_history[thread_id]) > MAX_HISTORY:
            chat_history[thread_id].pop(0)

    if (message.chat.type == "private" and message.from_user.id in ALLOWED_USERS) or current_chat_id == ALLOWED_GROUP:
        
        clean_request = message.text.replace(BOT_USERNAME, "").strip() if message.text else ""
        if not clean_request:
            clean_request = message.text

        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            full_prompt = (
                f"Before you is the history of the last messages from this working chat topic:\n"
                f"\"\"\"\n{context}\n\"\"\"\n\n"
                f"Fulfill the user's request based on this chat history: {clean_request}"
            )