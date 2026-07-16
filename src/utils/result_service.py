"""ID bo'yicha natijani yagona tartibda olish (barcha oqimlar uchun).

Tartib:
  1) natijalar jadvali — yakuniy natija bir marta saytdan olinadi,
     keyin doim shu yerdan qaytadi (saytga boshqa so'rov ketmaydi);
  2) Redis keshi — faqat "hali chiqmagan" javoblar uchun, qisqa muddat
     (natija chiqqan kechqurun eskirgan javob uzoq turmasligi kerak);
  3) sayt (fetch_details) — kelgan natijada umumiy ball bo'lsa,
     shu zahoti natijalar jadvaliga yoziladi (write-through).
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis

from src.db import database
from src.utils.mandat_parser import fetch_details, MandatUnavailable

# "Hali chiqmagan" javob keshining muddati
PENDING_TTL = 180  # 3 daqiqa
CACHE_PREFIX = "mandat:info:"

# Navbat + sayt uchun umumiy chegara: shundan uzoq kutgan so'rov
# "sayt javob bermayapti" deb yakunlanadi (fon vazifa baribir tugaydi
# va natija omborga tushadi — user qayta so'raganda darhol oladi)
FETCH_DEADLINE = 90  # soniya

redis = aioredis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


def is_final(info: dict) -> bool:
    """Umumiy ball chiqqan bo'lsa natija yakuniy hisoblanadi."""
    return bool(info.get("umumiy_ball"))


def _ball_to_num(info: dict) -> float | None:
    try:
        return float((info.get("umumiy_ball") or "").replace(",", "."))
    except ValueError:
        return None


async def get_result(abt_id: str) -> dict | None:
    """Natija lug'atini qaytaradi yoki None (ID saytda topilmadi).

    MandatBusy / MandatUnavailable fetch_details'dan yuqoriga otiladi.
    """
    row = await database.fetchone(
        "SELECT result_json FROM natijalar WHERE abt_id = %s", (abt_id,)
    )
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])

    try:
        cached = await redis.get(CACHE_PREFIX + abt_id)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logging.warning(f"Redis o'qish xatosi: {e}")

    try:
        info = await asyncio.wait_for(fetch_details(abt_id), timeout=FETCH_DEADLINE)
    except asyncio.TimeoutError:
        # Navbat juda uzun — userni cheksiz kuttirmaymiz. Saytga ketgan
        # fon so'rov bekor bo'lmaydi: tugagach natija omborga yoziladi.
        raise MandatUnavailable("kutish muddati tugadi (navbat uzun)")
    if info is None:
        return None

    if is_final(info):
        try:
            await save_final(info)
        except Exception:
            logging.exception(f"Natijani bazaga yozib bo'lmadi (ID={abt_id})")
    else:
        try:
            await redis.set(CACHE_PREFIX + abt_id, json.dumps(info), ex=PENDING_TTL)
        except Exception as e:
            logging.warning(f"Redis yozish xatosi: {e}")
    return info


async def save_final(info: dict) -> None:
    """Yakuniy natijani doimiy saqlaydi va buyurtmalardagi ballni to'ldiradi."""
    ball = _ball_to_num(info)
    await database.execute(
        """
        INSERT INTO natijalar (abt_id, fio, umumiy_ball, result_json)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (abt_id) DO NOTHING
        """,
        (info["abt_id"], info.get("fio"), ball, json.dumps(info)),
    )
    await database.execute(
        "UPDATE bhm SET umumiy_ball = %s WHERE abt_id = %s AND umumiy_ball IS NULL",
        (ball, info["abt_id"]),
    )
