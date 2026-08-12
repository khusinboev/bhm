"""Bot sozlamalari — kalit/qiymat ombori.

Admin buyruqlari orqali o'rnatiladigan qiymatlar (masalan WebApp havolasi)
shu yerda turadi va bot qayta ishga tushsa ham saqlanib qoladi.

MUHIM: sozlamalar UMUMIY bazada saqlanadi (.env dagi SHARED_DB_NAME).
Shu tufayli admin bitta botda /adwep bersa, havola uchala nusxada ham
ishlaydi. SHARED_DB_NAME berilmagan bo'lsa nusxaning o'z bazasi ishlatiladi
(bitta bot bo'lgan holat).

Har nusxa qiymatni o'z Redis'ida qisqa muddat keshlaydi — boshqa botda
o'zgartirilgan havola ko'pi bilan CACHE_TTL ichida ko'rinadi.
"""

import asyncio
import logging
import os

import psycopg2
import redis.asyncio as aioredis
from psycopg2.pool import ThreadedConnectionPool

from config import DB_CONFIG, REDIS_DB

CACHE_PREFIX = "mandat:setting:"
CACHE_TTL = 60      # o'rnatilgan qiymat keshi
# "O'rnatilmagan" holat qisqa keshlanadi: boshqa botda qo'yilgan havola
# deyarli darhol ko'rinsin (aks holda TTL tugashini kutish kerak bo'lardi)
EMPTY_TTL = 10

SHARED_DB_NAME = os.getenv("SHARED_DB_NAME") or DB_CONFIG["dbname"]
_SHARED_CFG = {**DB_CONFIG, "dbname": SHARED_DB_NAME}

redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)

_pool: ThreadedConnectionPool | None = None
_sem = asyncio.Semaphore(3)
_ready = False


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(minconn=1, maxconn=3, **_SHARED_CFG)
    return _pool


def _run(query: str, params=None, fetch: bool = False):
    global _ready
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            if not _ready:
                cur.execute("""CREATE TABLE IF NOT EXISTS public.sozlamalar (
                    kalit TEXT PRIMARY KEY,
                    qiymat TEXT,
                    updated_at TIMESTAMP DEFAULT NOW()
                )""")
                _ready = True
            cur.execute(query, params)
            row = cur.fetchone() if fetch else None
        conn.commit()
        return row
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        pool.putconn(conn, close=True)
        conn = None
        raise
    finally:
        if conn is not None:
            pool.putconn(conn)


async def _db(query: str, params=None, fetch: bool = False):
    async with _sem:
        return await asyncio.to_thread(_run, query, params, fetch)


async def get(key: str, default: str | None = None) -> str | None:
    try:
        cached = await redis.get(CACHE_PREFIX + key)
        if cached is not None:
            return cached or default  # bo'sh satr — "o'rnatilmagan"
    except Exception as e:
        logging.warning(f"Sozlamani Redis'dan o'qib bo'lmadi: {e}")

    try:
        row = await _db("SELECT qiymat FROM public.sozlamalar WHERE kalit = %s",
                        (key,), fetch=True)
    except Exception:
        logging.exception(f"Sozlamani bazadan o'qib bo'lmadi (kalit={key})")
        return default

    value = row[0] if row and row[0] else None
    try:
        await redis.set(CACHE_PREFIX + key, value or "",
                        ex=CACHE_TTL if value else EMPTY_TTL)
    except Exception as e:
        logging.warning(f"Sozlamani Redis'ga yozib bo'lmadi: {e}")
    return value if value else default


async def set_value(key: str, value: str) -> None:
    await _db("""
        INSERT INTO public.sozlamalar (kalit, qiymat, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (kalit) DO UPDATE
            SET qiymat = EXCLUDED.qiymat, updated_at = NOW()
    """, (key, value))
    try:
        await redis.set(CACHE_PREFIX + key, value, ex=CACHE_TTL)
    except Exception as e:
        logging.warning(f"Sozlamani Redis'da yangilab bo'lmadi: {e}")


async def delete(key: str) -> None:
    await _db("DELETE FROM public.sozlamalar WHERE kalit = %s", (key,))
    try:
        await redis.delete(CACHE_PREFIX + key)
    except Exception as e:
        logging.warning(f"Sozlama keshini o'chirib bo'lmadi: {e}")


async def close() -> None:
    global _pool
    if _pool is not None:
        await asyncio.to_thread(_pool.closeall)
        _pool = None
