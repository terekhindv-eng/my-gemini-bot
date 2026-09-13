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
    # Исправленная строка запуска (через точку, без знака равно)
    asyncio.run(main())