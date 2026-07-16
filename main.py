import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetUpdates
from aiogram.types import ErrorEvent

from config import BOT_TOKEN, dp, bot
from src.db import database
from src.db.init_db import create_all_base
from src.utils import mandat_parser
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
    await on_startup()
    logging.basicConfig(level=logging.INFO)

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

    try:
        # Navbatda qolgan eski update'larni tashlab yuborish (majburiy emas)
        await bot(GetUpdates(offset=-1, timeout=0))
    except Exception as e:
        logging.warning(f"Eski update'larni tashlab bo'lmadi, davom etamiz: {e}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())