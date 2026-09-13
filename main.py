import os
import io
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import google.generativeai as genai
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

try:
    ALLOWED_GROUP = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
except ValueError:
    ALLOWED_GROUP = 0

# Задача 2: Включаем форматирование Markdown по умолчанию для всех ответов бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
genai.configure(api_key=GEMINI_KEY)

# Актуальная модель Gemini
model = genai.GenerativeModel("gemini-3.6-flash")

MAX_HISTORY = 40
chat_history = []

BOT_USERNAME = ""
BOT_ID = 0

# Задача 4: Системная инструкция для точного копирования стиля оригинального Google AI (веб-версии)
GOOGLE_AI_SYSTEM_INSTRUCTION = (
    "Вы — официальный ИИ-ассистент Gemini от Google. Ваши ответы должны полностью "
    "соответствовать стилистике веб-интерфейса Google AI: будьте максимально полезным, "
    "конкретным, технологичным и точным. Избегайте пространных вступлений и дежурных фраз. "
    "Используйте структурированные списки и выделение важного текста жирным шрифтом, если это "
    "помогает восприятию информации. Пишите в профессиональном, но дружелюбном тоне."
)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if message.chat.type in ["group", "supergroup"] and message.chat.id != ALLOWED_GROUP:
        try:
            await message.answer("❌ Этот бот приватный и не может работать в данной группе.")
            await bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return
        
    await message.answer("Привет! Я официальный ассистент Gemini. Чем могу помочь?")

# Задача 3: Создаем хэндлер для обработки фотографий и файлов (документов)
@dp.message(F.photo | F.document)
async def handle_files(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id

    # Защита от чужих групп
    if message.chat.type in ["group", "supergroup"] and current_chat_id != ALLOWED_GROUP:
        try:
            await bot.leave_chat(current_chat_id)
        except Exception:
            pass
        return

    # Извлекаем текст (подпись к фото/файлу)
    user_text = message.caption if message.caption else ""
    
    # Записываем в общую историю чата для сохранения контекста
    file_type_label = "[Фотография]" if message.photo else "[Документ]"
    if message.chat.type in ["group", "supergroup"]:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history.append(f"{user_name}: {file_type_label} {user_text}")
        if len(chat_history) > MAX_HISTORY:
            chat_history.pop(0)

    # Скачиваем файл во временный буфер
    file_io = io.BytesIO()
    
    if message.photo:
        # Берем самое лучшее качество фотографии (последний элемент массива)
        file_info = message.photo[-1]
        mime_type = "image/jpeg"
    else:
        file_info = message.document
        mime_type = message.document.mime_type or "application/octet-stream"

    try:
        await bot.download(file_info, destination=file_io)
        file_bytes = file_io.getvalue()
    except Exception as e:
        await message.reply(f"❌ Не удалось загрузить файл: {str(e)}")
        return

    # Структурируем содержимое для Gemini API
    contents = [
        {
            "mime_type": mime_type,
            "data": file_bytes
        }
    ]

    # Формируем запрос с учетом контекста истории
    if message.chat.type != "private" and chat_history:
        context = "\n".join(chat_history)
        prompt_text = (
            f"Перед тобой история последних сообщений из рабочего чата:\n"
            f"\"\"\"\n{context}\n\"\"\"\n\n"
            f"Пользователь прикрепил файл и оставил запрос: {user_text}\n"
            f"Проанализируй прикрепленный файл, опираясь на контекст беседы."
        )
    else:
        prompt_text = user_text if user_text else "Проанализируй этот файл и детально опиши его содержимое."

    contents.append(prompt_text)

    # Отправляем в Gemini с применением настроек стиля
    await send_to_gemini(message, contents)


# Обработчик обычных текстовых сообщений
@dp.message()
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id

    # Защита от чужих групп
    if message.chat.type in ["group", "supergroup"] and current_chat_id != ALLOWED_GROUP:
        try:
            await bot.leave_chat(current_chat_id)
        except Exception:
            pass
        return

    # Записываем текущее сообщение в историю
    if message.chat.type in ["group", "supergroup"] and message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history.append(f"{user_name}: {message.text}")
        if len(chat_history) > MAX_HISTORY:
            chat_history.pop(0)

    # Задача 1: Бот теперь отвечает на ВСЕ сообщения в разрешенной группе или в ЛС
    if message.chat.type == "private" or current_chat_id == ALLOWED_GROUP:
        
        # Очищаем текст от упоминания бота (если оно было)
        clean_request = message.text.replace(BOT_USERNAME, "").strip() if message.text else ""
        if not clean_request:
            clean_request = message.text

        # Формируем итоговый промпт для текстовой модели
        if message.chat.type != "private" and chat_history:
            context = "\n".join(chat_history)
            full_prompt = (
                f"Перед тобой история последних сообщений из рабочего чата:\n"
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
            # Передаем системную инструкцию стиля через конфигурацию запроса
            response = model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(
                    system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION,
                    temperature=0.7
                )
            )
            
            # Если ответ пустой
            if not response.text:
                await message.reply("🔄 Извините, не удалось сгенерировать ответ. Попробуйте еще раз.")
                return

            await message.reply(response.text)
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
    asyncio.run(main())
