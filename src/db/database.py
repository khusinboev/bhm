"""Async-xavfsiz PostgreSQL qatlami.

config.py'dagi yagona global kursor o'rniga ThreadedConnectionPool ishlatiladi:
  - har bir so'rov pool'dan alohida ulanish oladi (parallel handlerlar aralashmaydi)
  - so'rovlar asyncio.to_thread ichida bajariladi (event loop bloklanmaydi)
  - uzilgan ulanish aniqlansa, yangi ulanish bilan bir marta qayta uriniladi
"""

import asyncio
import logging

from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import ThreadedConnectionPool

from config import DB_CONFIG

# Serverda bir nechta bot nusxasi + tarqat_worker + boshqa loyihalar bitta
# Postgres'ni bo'lishadi (max_connections=100). Har nusxa ko'pi bilan
# POOL_MAX ta ulanish oladi: 3 bot + worker = ~52, boshqalarga joy qoladi.
POOL_MAX = 12

# psycopg2 pool to'lganda KUTMAYDI — darhol PoolError otadi. Shuning uchun
# bir vaqtdagi so'rovlar sonini pool hajmidan past ushlaymiz: ortiqchasi
# navbatda muloyim kutadi, xato umuman chiqmaydi.
DB_CONCURRENCY = POOL_MAX - 2

_pool: ThreadedConnectionPool | None = None
_sem = asyncio.Semaphore(DB_CONCURRENCY)


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(minconn=1, maxconn=POOL_MAX, **DB_CONFIG)
    return _pool


def _execute_once(conn, query: str, params, fetch: str | None):
    with conn.cursor() as cur:
        cur.execute(query, params)
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        else:
            result = None
    conn.commit()
    return result


def _run(query: str, params, fetch: str | None):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        try:
            return _execute_once(conn, query, params, fetch)
        except (OperationalError, InterfaceError) as e:
            # Ulanish uzilgan bo'lishi mumkin — yangisi bilan bir marta qayta urinamiz
            logging.warning(f"DB ulanishi uzildi, qayta urinilmoqda: {e}")
            pool.putconn(conn, close=True)
            conn = None
            conn = pool.getconn()
            return _execute_once(conn, query, params, fetch)
    finally:
        if conn is not None:
            pool.putconn(conn)


async def fetchone(query: str, params=None):
    async with _sem:
        return await asyncio.to_thread(_run, query, params, "one")


async def fetchall(query: str, params=None):
    async with _sem:
        return await asyncio.to_thread(_run, query, params, "all")


async def execute(query: str, params=None) -> None:
    async with _sem:
        await asyncio.to_thread(_run, query, params, None)


async def close_pool() -> None:
    """Bot to'xtaganda pool'dagi barcha ulanishlarni yopish uchun."""
    global _pool
    if _pool is not None:
        await asyncio.to_thread(_pool.closeall)
        _pool = None
