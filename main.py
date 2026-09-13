import os, io, asyncio, threading, http.server, urllib.parse, urllib.request
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

def check_chat(m): return not (m.chat.type == "private" and m.from_user.id != 490524856) and not (m.chat.type in ["group", "supergroup"] and m.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()))

# 1. ГЛАВНЫЙ ХЭНДЛЕР: Исправленная и на 100% валидная ссылка для скачивания изображений
@dp.message(Command("draw", "рендери"))
async def generate_image_cmd(message: types.Message, command: CommandObject):
    if not check_chat(message): return
    if not command.args: return await message.reply("❌ Введите описание! Пример: <code>/draw космос</code>", parse_mode=ParseMode.HTML)
    
    status_msg = await message.reply("🎨 <i>Генерирую и скачиваю изображение, пожалуйста, подождите...</i>", parse_mode=ParseMode.HTML)
    try:
        clean_prompt = command.args.strip()
        encoded_prompt = urllib.parse.quote(clean_prompt)
        
        # Исправлено: Добавлен обязательный слеш после .ai/p/ для корректного пути URL
        image_url = f"https://pollinations.ai{encoded_prompt}?width=1024&height=1024&nologo=true&enhance=true"
        
        def download_file():
            req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
                
        image_bytes = await asyncio.to_thread(download_file)

        if not image_bytes:
            return await status_msg.edit_text("🔄 Ошибка: не удалось получить данные от сервера генерации.")

        input_file = types.BufferedInputFile(image_bytes, filename="generated_image.jpg")
        
        await message.reply_photo(
            photo=input_file, 
            caption=f"✨ <b>Готово!</b>\nЗапрос: <i>{clean_prompt}</i>", 
            parse_mode=ParseMode.HTML
        )
        await bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e: 
        await status_msg.edit_text(f"❌ Ошибка генерации:\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)

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
    print(f"Бот {BOT_USERNAME} запущен!"); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
