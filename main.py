import os
import asyncio
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

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    await message.answer("Привет! Я готов к работе с Gemini AI.")

@dp.message()
async def handle_message(message: types.Message):
    # Получаем юзернейм бота, чтобы искать его в тексте
    bot_info = await bot.get_me()
    bot_username = f"@{bot_info.username}"

    # Проверяем, обратились ли к боту
    is_mentioned = message.text and bot_username in message.text
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot_info.id

    # В личке отвечаем всегда. В группе — только если тегнули или ответили на его сообщение
    if message.chat.type == "private" or (message.chat.id == GROUP_ID and (is_mentioned or is_reply_to_bot)):
        try:
            # Очищаем текст вопроса от юзернейма бота, чтобы не путать нейросеть
            clean_text = message.text.replace(bot_username, "").strip() if message.text else ""
            
            if not clean_text and is_reply_to_bot:
                clean_text = message.text  # Если просто ответили на сообщение бота текстом

            response = model.generate_content(clean_text)
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

    print("Бот успешно запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
