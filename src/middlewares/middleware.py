import logging
from datetime import datetime

import pytz
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import Update

from src.db import database
from src.utils import known_users


class RegisterUserMiddleware(BaseMiddleware):
    """Har bir yangi foydalanuvchini accounts jadvaliga yozib boradi.

    Ro'yxatga olishdagi xatolik hech qachon asosiy handlerni to'xtatmasligi kerak,
    shuning uchun butun mantiq try/except ichida.
    """

    async def __call__(self, handler, event: Update, data: dict):
        user = None
        if event.message:
            user = event.message.from_user
        elif event.callback_query:
            user = event.callback_query.from_user

        if user is not None:
            try:
                # Redis'da "ma'lum" deb belgilangan user uchun Postgres'ga
                # umuman borilmaydi — bu har update'dagi eng gavjum yo'l
                if not await known_users.is_known(user.id):
                    date = datetime.now(pytz.timezone("Asia/Tashkent")).date()
                    lang_code = user.language_code or "uz"
                    # Bitta so'rov bilan: mavjud bo'lmasa qo'shadi (alohida SELECT+INSERT shart emas)
                    await database.execute(
                        """
                        INSERT INTO public.accounts (user_id, lang_code, date)
                        SELECT %s, %s, %s
                        WHERE NOT EXISTS (SELECT 1 FROM public.accounts WHERE user_id = %s)
                        """,
                        (user.id, lang_code, date, user.id),
                    )
                    await known_users.mark_known(user.id)
            except Exception:
                logging.exception("Foydalanuvchini ro'yxatga olishda xatolik")

        return await handler(event, data)
