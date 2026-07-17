"""Ro'yxatdan o'tgan foydalanuvchilar uchun Redis to'plami.

Har update'da Postgres'ga borib "bu user bazada bormi" deb tekshirish
o'rniga SISMEMBER ishlatiladi — Postgres'ga faqat chindan yangi user
(yoki Redis noaniq javob bergan holat) uchun boriladi.
Redis o'chsa/xato bersa — xavfsiz tomonga: haqiqiy tekshiruv talab qilinadi.
"""

import logging

import redis.asyncio as aioredis

from config import REDIS_DB
from src.db import database

KEY = "mandat:known_users"
redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)


async def preload() -> int:
    """Startup'da mavjud userlarni Redis to'plamiga bir martalik yuklaydi."""
    rows = await database.fetchall("SELECT user_id FROM public.accounts")
    if not rows:
        return 0
    ids = [str(r[0]) for r in rows]
    try:
        for i in range(0, len(ids), 5000):
            await redis.sadd(KEY, *ids[i:i + 5000])
    except Exception as e:
        logging.warning(f"Known-users preload xatosi: {e}")
    return len(ids)


async def is_known(user_id: int) -> bool:
    """True — Redis'da bor deb ma'lum; False — yo'q yoki Redis noaniq javob berdi."""
    try:
        return bool(await redis.sismember(KEY, str(user_id)))
    except Exception as e:
        logging.warning(f"Redis SISMEMBER xatosi: {e}")
        return False


async def mark_known(user_id: int) -> None:
    try:
        await redis.sadd(KEY, str(user_id))
    except Exception as e:
        logging.warning(f"Redis SADD xatosi: {e}")
