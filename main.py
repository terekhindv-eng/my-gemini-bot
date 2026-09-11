import os
import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import google.generativeai as genai
from aiohttp import web

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

bot = Bot(token=TOKEN)
dp = Dispatcher()
genai.configure(api_key=GEMINI_KEY)

# Обновляем до актуальной версии модели, если применимо
model = genai.GenerativeModel("gemini-3.6-flash") 

# Локальное хранилище для истории сообщений группы {chat_id: [list_of_messages]}
MAX_HISTORY = 40
chat_histories = defaultdict(list)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    # Защита: не даем запускать команду в чужих группах
    if message.chat.type in ["group", "supergroup"] and message.chat.id != GROUP_ID:
        await bot.leave_chat(message.chat.id)
        return
        
    await message.answer("Привет! Я готов к живому человеческому общению без лишних символов.")

@dp.message()
async def handle_message(message: types.Message):
    bot_info = await bot.get_me()
    bot_username = f"@{bot_info.username}"

    # ================= ЗАЩИТНЫЙ БЛОК НАЧАЛО =================
    # Если бот запущен в группе/супергруппе, и это НЕ ваша разрешенная группа
    if message.chat.type in ["group", "supergroup"] and message.chat.id != GROUP_ID:
        try:
            # Вежливо предупреждаем и выходим из чужого чата
            await message.answer("❌ Этот бот приватный и не может работать в данной группе.")
            await bot.leave_chat(message.chat.id)
        except Exception:
            pass # Если у бота нет прав писать сообщения, просто игнорируем ошибку
        return
    # ================= ЗАЩИТНЫЙ БЛОК КОНЕЦ =================

    # 1. Записываем текущее сообщение в историю
    if message.chat.id == GROUP_ID and message.text and bot_username not in message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_histories[GROUP_ID].append(f"{user_name}: {message.text}")
        if len(chat_histories[GROUP_ID]) > MAX_HISTORY:
            chat_histories[GROUP_ID].pop(0)

    # 2. Проверяем обращение к боту
    is_mentioned = message.text and bot_username in message.text
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot_info.id

    if message.chat.type == "private" or (message.chat.id == GROUP_ID and (is_mentioned or is_reply_to_bot)):
        try:
            clean_request = message.text.replace(bot_username, "").strip() if message.text else ""
            if not clean_request and is_reply_to_bot:
                clean_request = message.text

            # Инструкция для ведения живого диалога
            style_instruction = (
                "\n\nПРАВИЛО СТИЛЯ И ОФОРМЛЕНИЯ:\n"
                "Отвечай как живой человек в обычном текстовом чате или мессенджере. "
                "Пиши связным текстом, разделяя мысли на обычные абзацы. "
                "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать любые символы звездочек (*), решеток (#) или дефисов в начале строк. "
                "Не делай маркированных или нумерованных списков. Твой ответ должен выглядеть как естественная реплика в диалоге."
            )

            # Формируем итоговый промпт
            if message.chat.type != "private" and chat_histories[GROUP_ID]:
                context = "\n".join(chat_histories[GROUP_ID])
                full_prompt = (
                    f"Перед тобой история последних сообщений из рабочего чата:\n"
                    f"\"\"\"\n{context}\n\"\"\"\n\n"
                    f"Выполни запрос пользователя, опираясь на эту историю чата: {clean_request}"
                    f"{style_instruction}"
                )
            else:
                full_prompt = f"{clean_request}{style_instruction}"

            response = model.generate_content(full_prompt)
            await message.reply(response.text)
            
        except Exception as e:
            await message.reply(f"Ошибка Gemini API: {str(e)}")

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    asyncio.create_task(site.start())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
