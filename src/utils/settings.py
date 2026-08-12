"""Bot sozlamalari — bazada saqlanadigan oddiy kalit/qiymat ombori.

Admin buyruqlari orqali o'zgartiriladigan qiymatlar (masalan WebApp havolasi)
shu yerda turadi: bot qayta ishga tushsa ham saqlanib qoladi.
Redis keshi tufayli har bosishda bazaga borilmaydi.
"""

import logging

import redis.asyncio as aioredis

from config import REDIS_DB
from src.db import database

CACHE_PREFIX = "mandat:setting:"
CACHE_TTL = 300  # 5 daqiqa (o'zgartirilganda kesh darhol yangilanadi)

redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)


async def get(key: str, default: str | None = None) -> str | None:
    try:
        cached = await redis.get(CACHE_PREFIX + key)
        if cached is not None:
            return cached or default  # bo'sh satr — "o'rnatilmagan"
    except Exception as e:
        logging.warning(f"Sozlamani Redis'dan o'qib bo'lmadi: {e}")

    try:
        row = await database.fetchone(
            "SELECT qiymat FROM public.sozlamalar WHERE kalit = %s", (key,))
    except Exception:
        logging.exception(f"Sozlamani bazadan o'qib bo'lmadi (kalit={key})")
        return default

    value = row[0] if row and row[0] else None
    try:
        await redis.set(CACHE_PREFIX + key, value or "", ex=CACHE_TTL)
    except Exception as e:
        logging.warning(f"Sozlamani Redis'ga yozib bo'lmadi: {e}")
    return value if value else default


async def set_value(key: str, value: str) -> None:
    await database.execute(
        """
        INSERT INTO public.sozlamalar (kalit, qiymat, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (kalit) DO UPDATE
            SET qiymat = EXCLUDED.qiymat, updated_at = NOW()
        """,
        (key, value),
    )
    try:
        await redis.set(CACHE_PREFIX + key, value, ex=CACHE_TTL)
    except Exception as e:
        logging.warning(f"Sozlamani Redis'da yangilab bo'lmadi: {e}")


async def delete(key: str) -> None:
    await database.execute("DELETE FROM public.sozlamalar WHERE kalit = %s", (key,))
    try:
        await redis.delete(CACHE_PREFIX + key)
    except Exception as e:
        logging.warning(f"Sozlama keshini o'chirib bo'lmadi: {e}")
