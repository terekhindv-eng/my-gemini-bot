import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
import google.generativeai as genai
from aiohttp import web

# Переменные
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
    if message.chat.type == "private" or message.chat.id == GROUP_ID:
        try:
            response = model.generate_content(message.text)
            await message.reply(response.text)
        except Exception as e:
            await message.reply(f"Ошибка Gemini API: {str(e)}")

# Заглушка веб-сервера для Render
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    # Запуск веб-сервера на порту Render
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
