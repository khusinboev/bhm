"""Telegram'ga chidamli javob yuborish.

Qimmatga tushgan javoblar (sayt so'rovidan keyingi natija) uchun:
  - flood-wait (RetryAfter) kelsa kutib bir marta qayta uriniladi;
  - user xabar yuborib darhol botni bloklagan bo'lsa (Forbidden) —
    xato yutiladi, bot ishlashda davom etadi;
  - boshqa xatolar logga yoziladi, handler yiqilmaydi.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import Message


async def answer_safe(message: Message, text: str, **kwargs) -> bool:
    """True — yuborildi; False — yuborib bo'lmadi (bot ishlayveradi)."""
    try:
        await message.answer(text, **kwargs)
        return True
    except TelegramRetryAfter as e:
        await asyncio.sleep(min(e.retry_after, 30) + 0.5)
        try:
            await message.answer(text, **kwargs)
            return True
        except Exception as e2:
            logging.warning(f"Flood-wait'dan keyin ham yuborilmadi (chat={message.chat.id}): {e2}")
            return False
    except TelegramForbiddenError:
        logging.info(f"User botni bloklagan (chat={message.chat.id}) — javob tashlab yuborildi")
        return False
    except Exception:
        logging.exception(f"Xabar yuborishda kutilmagan xato (chat={message.chat.id})")
        return False
