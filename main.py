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
model = genai.GenerativeModel("gemini-3.6-flash")

# Локальное хранилище для истории сообщений группы {chat_id: [list_of_messages]}
MAX_HISTORY = 40
chat_histories = defaultdict(list)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer("Привет! Я записываю контекст группы и готов проанализировать его по вашему запросу.")

@dp.message()
async def handle_message(message: types.Message):
    bot_info = await bot.get_me()
    bot_username = f"@{bot_info.username}"

    # 1. Записываем текущее сообщение в историю, если оно из целевой группы и это не вызов бота
    if message.chat.id == GROUP_ID and message.text and bot_username not in message.text:
        user_name = message.from_user.full_name or "Пользователь"
        chat_histories[GROUP_ID].append(f"{user_name}: {message.text}")
        if len(chat_histories[GROUP_ID]) > MAX_HISTORY:
            chat_histories[GROUP_ID].pop(0)

    # 2. Проверяем, обратился ли пользователь к боту в личке или группе
    is_mentioned = message.text and bot_username in message.text
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot_info.id

    if message.chat.type == "private" or (message.chat.id == GROUP_ID and (is_mentioned or is_reply_to_bot)):
        try:
            clean_request = message.text.replace(bot_username, "").strip() if message.text else ""
            if not clean_request and is_reply_to_bot:
                clean_request = message.text

            # Формируем контекст для Gemini из оперативной памяти
            if message.chat.type != "private" and chat_histories[GROUP_ID]:
                context = "\n".join(chat_histories[GROUP_ID])
                full_prompt = (
                    f"Перед тобой история последних сообщений из рабочего чата:\n"
                    f"\"\"\"\n{context}\n\"\"\"\n\n"
                    f"Выполни следующий запрос пользователя, опираясь на эту историю чата: {clean_request}"
                )
            else:
                full_prompt = clean_request

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

    print("Бот успешно запущен с поддержкой анализа контекста...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
