import os
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import google.generativeai as genai
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", "10000"))

# Работаем строго с ОДНОЙ группой (если переменная пустая или не число, запишется 0)
try:
    ALLOWED_GROUP = int(os.getenv("TELEGRAM_GROUP_ID", "0").strip())
except ValueError:
    ALLOWED_GROUP = 0

bot = Bot(token=TOKEN)
dp = Dispatcher()
genai.configure(api_key=GEMINI_KEY)

# Используем актуальную модель gemini-3.6-flash
model = genai.GenerativeModel("gemini-3.6-flash")

# Локальное хранилище истории для нашей группы
MAX_HISTORY = 40
chat_history = []

# Глобальные переменные данных бота
BOT_USERNAME = ""
BOT_ID = 0

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    # Защита: если команду вызвали в чужой группе
    if message.chat.type in ["group", "supergroup"] and message.chat.id != ALLOWED_GROUP:
        try:
            await message.answer("❌ Этот бот приватный и не может работать в данной группе.")
            await bot.leave_chat(message.chat.id)
        except Exception:
            pass
        return
        
    await message.answer("Привет! Я готов к живому человеческому общению без лишних символов.")

@dp.message()
async def handle_message(message: types.Message):
    global BOT_USERNAME, BOT_ID
    current_chat_id = message.chat.id

    # ================= БЛОК ЗАЩИТЫ ОТ ЧУЖИХ ГРУПП =================
    if message.chat.type in ["group", "supergroup"] and current_chat_id != ALLOWED_GROUP:
        try:
            await message.answer("❌ Этот бот приватный и не может работать в данной группе.")
            await bot.leave_chat(current_chat_id)
        except Exception:
            pass
        return
    # ==============================================================

    # 1. Записываем текущее сообщение в историю
    if message.chat.type in ["group", "supergroup"] and message.text and BOT_USERNAME not in message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_history.append(f"{user_name}: {message.text}")
        if len(chat_history) > MAX_HISTORY:
            chat_history.pop(0)

    # 2. Проверяем, обратился ли кто-то к боту
    is_mentioned = message.text and BOT_USERNAME in message.text
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID

    # Бот реагирует, если это ЛС или разрешенная группа + упомянули/ответили боту
    if message.chat.type == "private" or (current_chat_id == ALLOWED_GROUP and (is_mentioned or is_reply_to_bot)):
        
        clean_request = message.text.replace(BOT_USERNAME, "").strip() if message.text else ""
        if not clean_request and is_reply_to_bot:
            clean_request = message.text

        # Правило стиля для Gemini
        style_instruction = (
            "\n\nПРАВИЛО СТИЛЯ И ОФОРМЛЕНИЯ:\n"
            "Отвечай как живой человек в обычном текстовом чате или мессенджере. "
            "Пиши связным текстом, разделяя мысли на обычные абзацы. "
            "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать любые символы звездочек (*), решеток (#) или дефисов в начале строк. "
            "Не делай маркированных или нумерованных списков. Твой ответ должен выглядеть как естественная реплика в диалоге."
        )

        # Формируем промпт
        if message.chat.type != "private" and chat_history:
            context = "\n".join(chat_history)
            full_prompt = (
                f"Перед тобой история последних сообщений из рабочего чата:\n"
                f"\"\"\"\n{context}\n\"\"\"\n\n"
                f"Выполни запрос пользователя, опираясь на эту историю чата: {clean_request}"
                f"{style_instruction}"
            )
        else:
            full_prompt = f"{clean_request}{style_instruction}"

        # Попытки отправки запроса с обработкой лимитов (ошибка 429)
        for attempt in range(3):
            try:
                response = model.generate_content(full_prompt)
                await message.reply(response.text)
                return  # Успешно отправили, выходим из функции
                
            except Exception as e:
                err_msg = str(e)
                # Если упёрлись в лимиты частоты запросов Google Gemini
                if "429" in err_msg or "quota" in err_msg.lower():
                    if attempt < 2:
                        await asyncio.sleep(5)  # Ждем 5 секунд перед повторной попыткой
                        continue
                    else:
                        await message.reply("⏳ Извините, сейчас слишком много запросов к ИИ. Подождите минуту и повторите.")
                else:
                    # Любая другая критическая ошибка
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
