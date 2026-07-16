"""Majburiy kanallar ro'yxati uchun Redis kesh.

Yozish manbai — Postgres (mandatorys jadvali), user oqimidagi barcha
o'qishlar — Redis'dan. Admin kanal qo'shsa/o'chirsa refresh() darhol
yangilaydi; TTL tugasa ham keyingi o'qishda o'zi to'ldiriladi.
Redis ishlamay qolsa to'g'ridan-to'g'ri Postgres'dan o'qiladi — bot to'xtamaydi.
"""

import json
import logging

import redis.asyncio as aioredis

from src.db import database

KEY = "mandat:channels"
TTL = 600  # 10 daqiqa — refresh chaqirilmasa ham o'zi yangilanib turadi

redis = aioredis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


async def _load_from_db() -> list[list]:
    rows = await database.fetchall("SELECT chat_id, username FROM public.mandatorys")
    return [[r[0], r[1]] for r in rows]


async def get_channels() -> list[list]:
    """[[chat_id, username], ...] — avval Redis'dan, bo'lmasa Postgres'dan."""
    try:
        cached = await redis.get(KEY)
        if cached is not None:  # bo'sh ro'yxat ("[]") ham haqiqiy kesh
            return json.loads(cached)
    except Exception as e:
        logging.warning(f"Redis'dan kanallarni o'qib bo'lmadi: {e}")

    channels = await _load_from_db()
    try:
        await redis.set(KEY, json.dumps(channels), ex=TTL)
    except Exception as e:
        logging.warning(f"Redis'ga kanallarni yozib bo'lmadi: {e}")
    return channels


async def refresh() -> None:
    """Admin kanal qo'shganda/o'chirganda darhol yangilash."""
    channels = await _load_from_db()
    try:
        await redis.set(KEY, json.dumps(channels), ex=TTL)
    except Exception as e:
        logging.warning(f"Redis'da kanallarni yangilab bo'lmadi: {e}")
