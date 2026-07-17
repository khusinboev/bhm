import asyncio
import logging
import signal
import ssl

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetUpdates
from aiogram.types import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import (
    BOT_TOKEN, dp, bot,
    USE_WEBHOOK, WEBHOOK_HOST, WEBHOOK_PORT, WEBHOOK_PATH,
    WEBHOOK_SECRET, WEBHOOK_SSL_CERT, WEBHOOK_SSL_KEY,
)
from src.db import database
from src.db.init_db import create_all_base
from src.utils import known_users, mandat_parser
from src.handlers.admins.add_admin import add_router
from src.handlers.admins.admin import admin_router
from src.handlers.admins.messages import msg_router
from src.handlers.admins.tarqatish import tarqat_router
from src.handlers.others.channels import channel_router
from src.handlers.others.groups import group_router
from src.handlers.others.other import other_router
# from src.handlers.users.buyurtma import buyurtma_router
from src.handlers.users.data import data_router
from src.handlers.users.users import user_router
from src.middlewares.middleware import RegisterUserMiddleware


async def on_startup() -> None:
    await create_all_base()
    count = await known_users.preload()
    logging.info(f"Known-users kesh: {count} ta mavjud user Redis'ga yuklandi")


async def on_error(event: ErrorEvent) -> bool:
    # User xabar yuborib darhol botni bloklasa — bu odatiy holat,
    # to'liq traceback bilan jurnalni to'ldirmaymiz
    if isinstance(event.exception, TelegramForbiddenError):
        logging.warning("Bloklagan userga javob yuborib bo'lmadi (o'tkazib yuborildi)")
        return True
    logging.exception("Handlerda xatolik", exc_info=event.exception)
    return True


async def on_shutdown() -> None:
    # Yo'lda qolgan handlerlar tugashiga qisqa muhlat — aks holda ular
    # yopilgan pool/sessiyaga urilib "pool is closed" xatolari chiqaradi
    await asyncio.sleep(3)
    await mandat_parser.close_session()
    await database.close_pool()


async def main():
    logging.basicConfig(level=logging.INFO)
    await on_startup()

    dp.update.middleware(RegisterUserMiddleware())
    dp.shutdown.register(on_shutdown)
    dp.errors.register(on_error)

    #for admin
    dp.include_router(admin_router)
    dp.include_router(add_router)
    dp.include_router(msg_router)
    dp.include_router(tarqat_router)

    #for user
    dp.include_router(user_router)
    # dp.include_router(buyurtma_router)
    dp.include_router(data_router)

    #for other
    dp.include_router(group_router)
    dp.include_router(channel_router)
    dp.include_router(other_router)

    if USE_WEBHOOK:
        await run_webhook()
    else:
        await run_polling()


async def run_polling() -> None:
    try:
        # Webhook qolib ketgan bo'lsa polling u bilan to'qnashadi — olib tashlaymiz
        await bot.delete_webhook(drop_pending_updates=False)
        # Navbatda qolgan eski update'larni tashlab yuborish (majburiy emas)
        await bot(GetUpdates(offset=-1, timeout=0))
    except Exception as e:
        logging.warning(f"Polling'ga tayyorgarlikda xato, davom etamiz: {e}")
    await dp.start_polling(bot)


async def run_webhook() -> None:
    """Webhook rejimi: bot jarayonining o'zida TLS'li aiohttp server (8443).

    nginx ishtirok etmaydi — talim24.uz'ning 80/443'dagi saytlariga tegilmaydi.
    Sertifikat: mavjud Let's Encrypt fayllari (config'da yo'li).
    """
    url = f"https://{WEBHOOK_HOST}:{WEBHOOK_PORT}{WEBHOOK_PATH}"

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET or None
    ).register(app, path=WEBHOOK_PATH)

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/health", health)
    # dp startup/shutdown hooklarini (jumladan on_shutdown) server hayotiga bog'laydi
    setup_application(app, dp, bot=bot)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(WEBHOOK_SSL_CERT, WEBHOOK_SSL_KEY)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT, ssl_context=ssl_ctx)
    await site.start()

    await bot.set_webhook(
        url,
        secret_token=WEBHOOK_SECRET or None,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )
    logging.info(f"Webhook o'rnatildi: {url}")

    # SIGTERM/SIGINT kelguncha ishlaymiz; keyin server toza yopiladi
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        await runner.cleanup()  # app cleanup -> dp shutdown -> on_shutdown


if __name__ == "__main__":
    asyncio.run(main())