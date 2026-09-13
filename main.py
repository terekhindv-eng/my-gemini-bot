import os
import io
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.enums import ParseMode
from google import genai
from google.genai import types as genai_types
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Инициализируем стандартный клиент Google GenAI
ai_client = genai.Client(api_key=GEMINI_KEY)

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

TEXT_CONFIG = genai_types.GenerateContentConfig(
    system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION,
    temperature=0.7
)

# Специальная конфигурация для команды рисования через запуск кода на серверах Google по стандартам нового SDK
DRAW_CONFIG = genai_types.GenerateContentConfig(
    system_instruction=(
        "Ты — генератор изображений. Твоя единственная задача — написать Python-код "
        "с использованием библиотек matplotlib или PIL, который визуализирует и рисует "
        "запрос пользователя, а затем сохранить результат в файл 'output.png'. "
        "Используй продвинутую графику, градиенты, геометрические фракталы или пиксель-арт, "
        "чтобы детально отобразить то, что просит пользователь."
    ),
    tools=[{'code_execution': {}}],
    temperature=0.3
)

MAX_HISTORY = 50
chat_history = defaultdict(list)

BOT_USERNAME = ""
BOT_ID = 0

# 1. ГЛАВНЫЙ ХЭНДЛЕР: Генерация графики силами самой модели Gemini (100% обход любых блокировок)
@dp.message(Command("draw", "рендери"))
async def generate_image_cmd(message: types.Message, command: CommandObject):
    current_chat_id = message.chat.id
    if message.chat.type == "private" and message.from_user.id != 490524856:
        return
    if message.chat.type in ["group", "supergroup"] and message.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()):
        return

    image_prompt = command.args
    if image_prompt:
        image_prompt = image_prompt.strip()

    if not image_prompt:
        await message.reply("❌ <b>Вы не ввели описание для картинки!</b>\nПример использования:\n<code>/draw космический город</code>", parse_mode=ParseMode.HTML)
        return

    status_msg = await message.reply("🎨 <i>Генерирую графику по вашему запросу на серверах Google, пожалуйста, подождите...</i>", parse_mode=ParseMode.HTML)

    try:
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[f"Нарисуй и сохрани в 'output.png': {image_prompt}"],
            config=DRAW_CONFIG
        )

        image_bytes = None
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            await status_msg.edit_text(f"🤖 <b>Ответ модели:</b>\n{response.text}", parse_mode=ParseMode.HTML)
            return

        input_file = types.BufferedInputFile(image_bytes, filename="generated_image.png")
        await bot.delete_message(chat_id=current_chat_id, message_id=status_msg.message_id)
        await message.reply_photo(photo=input_file, caption=f"✨ Готово! Графический рендер: <i>{image_prompt}</i>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка рендеринга: {str(e)}")

# 2. Хэндлер команды /start
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if message.chat.type == "private" and message.from_user.id != 490524856:
        await message.answer("❌ Общение с ботом в личных сообщениях запрещено. Бот работает только в рабочей группе.")
        return
    if message.chat.type in ["group", "supergroup"] and message.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()):
        try:
            await bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return
    await message.answer("Привет! Я официальный ассистент Gemini. Чем могу помочь?")

# 3. Хэндлер входящих файлов и документов
@dp.message(F.photo | F.document | F.audio | F.voice)
async def handle_files(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id
    if message.chat.type == "private" and message.from_user.id != 490524856:
        return
    if message.chat.type in ["group", "supergroup"] and message.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()):
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

    file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

    if message.chat.type != "private" and chat_history[thread_id]:
        context = "\n".join(chat_history[thread_id])
        prompt_text = f"История темы чата:\n{context}\n\nЗапрос к файлу: {user_text}"
    else:
        prompt_text = user_text if user_text else "Проанализируй содержимое этого медиафайла."

    await send_to_gemini(message, [file_part, prompt_text])

# 4. Хэндлер обычных текстовых сообщений
@dp.message(F.text)
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id
    if message.chat.type == "private" and message.from_user.id != 490524856:
        return
    if message.chat.type in ["group", "supergroup"] and message.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()):
        return

    thread_id = message.message_thread_id or 0

    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history[thread_id].append(f"{user_name}: {message.text}")
        if len(chat_history[thread_id]) > MAX_HISTORY:
            chat_history[thread_id].pop(0)

    if (message.chat.type == "private" and message.from_user.id == 490524856) or message.chat.type in ["group", "supergroup"]:
        clean_request = message.text.replace(BOT_USERNAME, "").strip() if message.text else ""
        if not clean_request:
            clean_request = message.text

        if message.chat.type != "private" and chat_history[thread_id]:
            context = "\n".join(chat_history[thread_id])
            full_prompt = f"История темы чата:\n{context}\n\nВыполни запрос пользователя: {clean_request}"
        else:
            full_prompt = clean_request

        await send_to_gemini(message, [full_prompt])

async def send_to_gemini(message: types.Message, contents: list):
    try:
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=contents,
            config=TEXT_CONFIG
        )
        if not response.text:
            await message.reply("🔄 Не удалось получить ответ. Попробуйте снова.")
            return
        raw_text = response.text.replace("**", "")
        await message.reply(raw_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply(f"Ошибка Gemini API: {str(e)}")

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    global BOT_USERNAME, BOT_ID
    bot_info = await bot.get_me()
    BOT_USERNAME = f"@{bot_info.username}"
    BOT_ID = bot_info.id
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Открываем веб-порт 10000 для прохождения проверки хостинга Render
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    print(f"Бот {BOT_USERNAME} успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
