import os, io, asyncio, threading, http.server
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.enums import ParseMode
from google import genai
from google.genai import types as genai_types

TOKEN, GEMINI_KEY, PORT = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("GEMINI_API_KEY"), int(os.getenv("PORT", "10000"))
bot, dp = Bot(token=TOKEN), Dispatcher()
ai_client = genai.Client(api_key=GEMINI_KEY)

GOOGLE_AI_SYSTEM_INSTRUCTION = "Вы — official Google Gemini AI. Запрещено использовать (*) или (_) для выделения текста. Если нужно сделать текст ЖИРНЫМ, используй теги <b>текст</b>, КУРСИВ — <i>текст</i>."
TEXT_CONFIG = genai_types.GenerateContentConfig(system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION, temperature=0.7)

# Конфигурация автономного рендеринга на серверах Google
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

def check_chat(m): 
    if m.chat.type == "private" and m.from_user.id != 490524856: return False
    if m.chat.type in ["group", "supergroup"] and m.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()): return False
    return True

# 1. ГЛАВНЫЙ ХЭНДЛЕР: Локальный рендеринг силами Google API (Без интернета на Render)
@dp.message(Command("draw", "рендери"))
async def generate_image_cmd(message: types.Message, command: CommandObject):
    if not check_chat(message): return
    if not command.args: return await message.reply("❌ Введите описание! Пример: <code>/draw космос</code>", parse_mode=ParseMode.HTML)
    
    status_msg = await message.reply("🎨 <i>Генерирую графику на серверах Google, пожалуйста, подождите...</i>", parse_mode=ParseMode.HTML)
    try:
        response = ai_client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[f"Нарисуй и сохрани в 'output.png': {command.args.strip()}"],
            config=DRAW_CONFIG
        )

        image_bytes = None
        # Безопасно вытаскиваем сгенерированные бинарные байты картинки из ответа Google
        if response.candidates and len(response.candidates) > 0:
            parts = response.candidates[0].content.parts
            if parts:
                for part in parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break

        if not image_bytes:
            # Если вернулся только текст или лог кода
            return await status_msg.edit_text(f"🤖 <b>Ответ модели:</b>\n{response.text or 'Не удалось построить график.'}")

        input_file = types.BufferedInputFile(image_bytes, filename="generated_image.png")
        await bot.delete_message(message.chat.id, status_msg.message_id)
        await message.reply_photo(photo=input_file, caption=f"✨ <b>Готово! Графический рендер собран.</b>\nЗапрос: <i>{command.args.strip()}</i>", parse_mode=ParseMode.HTML)
    except Exception as e: 
        await status_msg.edit_text(f"❌ Ошибка рендеринга:\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if check_chat(message): await message.answer("Привет! Я официальный ассистент Gemini. Чем могу помочь?")

@dp.message(F.photo | F.document | F.audio | F.voice)
async def handle_files(message: types.Message):
    if not check_chat(message): return
    txt = message.caption or "Проанализируй медиафайл."
    f_io = io.BytesIO()
    f_info = message.photo[-1] if message.photo else (message.voice if message.voice else (message.audio if message.audio else message.document))
    mime = "image/jpeg" if message.photo else (message.voice.mime_type if message.voice else (message.audio.mime_type if message.audio else message.document.mime_type))
    try:
        await bot.download(f_info, destination=f_io)
        await send_to_gemini(message, [genai_types.Part.from_bytes(data=f_io.getvalue(), mime_type=mime or "application/octet-stream"), txt])
    except Exception as e: await message.reply(f"❌ Сбой загрузки файла: {str(e)}")

@dp.message(F.text)
async def handle_message(message: types.Message):
    if check_chat(message): await send_to_gemini(message, [message.text])

async def send_to_gemini(message: types.Message, contents: list):
    try:
        res = ai_client.models.generate_content(model='gemini-3.6-flash', contents=contents, config=TEXT_CONFIG)
        await message.reply((res.text or "🔄 Пустой ответ.").replace("**", ""), parse_mode=ParseMode.HTML)
    except Exception as e: await message.reply(f"Ошибка API: {str(e)}")

def run_http_server():
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"Live")
    http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def main():
    global BOT_USERNAME
    info = await bot.get_me()
    BOT_USERNAME = f"@{info.username}"
    await bot.delete_webhook(drop_pending_updates=True)
    threading.Thread(target=run_http_server, daemon=True).start()
    print(f"Бот {BOT_USERNAME} успешно запущен!"); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
