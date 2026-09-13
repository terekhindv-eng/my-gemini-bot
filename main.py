import os, io, asyncio, re, multiprocessing
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.enums import ParseMode
from google import genai
from google.genai import types as genai_types

TOKEN, GEMINI_KEY, PORT = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("GEMINI_API_KEY"), int(os.getenv("PORT", "10000"))
bot, dp, ai_client = Bot(token=TOKEN), Dispatcher(), genai.Client(api_key=GEMINI_KEY)
chat_history, BOT_USERNAME, BOT_ID = asyncio.defaultdict(list), "", 0

GOOGLE_AI_SYSTEM_INSTRUCTION = "Вы — официальный ИИ-ассистент Gemini от Google. Тебе КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать звездочки (*) или нижние подчеркивания (_) для выделения текста. Если нужно сделать текст ЖИРНЫМ, используй строго теги <b>текст</b>, КУРСИВ — <i>текст</i>."
TEXT_CONFIG = genai_types.GenerateContentConfig(system_instruction=GOOGLE_AI_SYSTEM_INSTRUCTION, temperature=0.7)
DRAW_CONFIG = genai_types.GenerateContentConfig(
    system_instruction="Ты — генератор графики на Python. Напиши полноценный скрипт с использованием matplotlib или PIL, который визуализирует запрос пользователя и ОБЯЗАТЕЛЬНО сохраняет результат в 'output.png'. Используй продвинутый пиксель-арт или фигуры. Выводи код внутри ```python.",
    tools=[{'code_execution': {}}], temperature=0.3
)

def check_chat(m): return not (m.chat.type == "private" and m.from_user.id != 490524856) and not (m.chat.type in ["group", "supergroup"] and m.chat.id != int(os.getenv("TELEGRAM_GROUP_ID", "0").strip()))

@dp.message(Command("draw", "рендери"))
async def generate_image_cmd(message: types.Message, command: CommandObject):
    if not check_chat(message): return
    if not command.args: return await message.reply("❌ Введите описание! Пример: <code>/draw космос</code>", parse_mode=ParseMode.HTML)
    status_msg = await message.reply("🎨 <i>Генерирую графику на серверах Google...</i>", parse_mode=ParseMode.HTML)
    try:
        res = ai_client.models.generate_content(model='gemini-3.6-flash', contents=[f"Нарисуй и сохрани в 'output.png': {command.args.strip()}"], config=DRAW_CONFIG)
        img_bytes = None
        if res.candidates and res.candidates.content.parts:
            for p in res.candidates.content.parts:
                if p.inline_data: img_bytes = p.inline_data.data; break
        if not img_bytes and res.text:
            cb = re.search(r"```python(.*?)```", res.text, re.DOTALL)
            script = cb.group(1).strip() if cb else res.text
            if any(x in script for x in ["output.png", "plt", "Image"]):
                try:
                    loc = {}
                    exec(script, {}, loc)
                    if os.path.exists("output.png"):
                        with open("output.png", "rb") as f: img_bytes = f.read()
                        os.remove("output.png")
                except Exception: pass
        if not img_bytes: return await status_msg.edit_text(f"🤖 <b>Ответ модели:</b>\n{res.text or 'Ошибка.'}")
        await bot.delete_message(message.chat.id, status_msg.message_id)
        await message.reply_photo(photo=types.BufferedInputFile(img_bytes, filename="img.png"), caption=f"✨ Готово! Рендер: <i>{command.args.strip()}</i>", parse_mode=ParseMode.HTML)
    except Exception as e: await status_msg.edit_text(f"❌ Ошибка: {str(e)}")

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

def fake_server():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", PORT))
    s.listen(1)
    while True:
        try: conn, _ = s.accept(); conn.send(b"HTTP/1.1 200 OK\r\n\r\nBot is live!"); conn.close()
        except: pass

async def main():
    global BOT_USERNAME, BOT_ID
    info = await bot.get_me()
    BOT_USERNAME, BOT_ID = f"@{info.username}", info.id
    await bot.delete_webhook(drop_pending_updates=True)
    multiprocessing.Process(target=fake_server, daemon=True).start()
    print(f"Бот {BOT_USERNAME} запущен!"); await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
