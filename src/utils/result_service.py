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

# Yakuniy natija keshi: Postgres — doimiy manba, Redis — tezkor qatlam.
# Ommaviy so'ralgan ID'lar uchun Postgres'ga umuman borilmaydi.
FINAL_TTL = 6 * 3600  # 6 soat
FINAL_PREFIX = "mandat:final:"

# Har bir chaqiruvchining o'z kutish chegarasi: shundan uzoq kutgan so'rov
# "sayt javob bermayapti" deb yakunlanadi. Fon vazifa esa (shield tufayli)
# bekor bo'lmaydi — tugagach natija omborga yoziladi, user qayta so'raganda
# darhol oladi. Shu tufayli ko'pchilik "kuting" javobini tez oladi,
# sayt topganlari esa keyingi urinishda bir zumda natija ko'radi.
FETCH_DEADLINE = 20  # soniya

# Bir xil ID uchun "saytdan olish + omborga yozish" bitta umumiy fon vazifada
_inflight: dict[str, asyncio.Task] = {}

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
    try:
        cached_final = await redis.get(FINAL_PREFIX + abt_id)
        if cached_final:
            return json.loads(cached_final)
    except Exception as e:
        logging.warning(f"Redis'dan yakuniy natijani o'qib bo'lmadi: {e}")

    row = await database.fetchone(
        "SELECT result_json FROM natijalar WHERE abt_id = %s", (abt_id,)
    )
    if row:
        info = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        try:
            await redis.set(FINAL_PREFIX + abt_id, json.dumps(info), ex=FINAL_TTL)
        except Exception as e:
            logging.warning(f"Redis'ga yakuniy natijani yozib bo'lmadi: {e}")
        return info

    try:
        cached = await redis.get(CACHE_PREFIX + abt_id)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logging.warning(f"Redis o'qish xatosi: {e}")

    task = _inflight.get(abt_id)
    if task is None:
        task = asyncio.create_task(_fetch_and_store(abt_id))
        _inflight[abt_id] = task
        task.add_done_callback(lambda _t, _id=abt_id: _inflight.pop(_id, None))
    try:
        # shield — bitta userning deadline'i umumiy fon vazifani (va u orqali
        # boshqa userlarning kutishini) bekor qilmasligi uchun. Har kim faqat
        # O'ZINING kutishini to'xtatadi.
        return await asyncio.wait_for(asyncio.shield(task), timeout=FETCH_DEADLINE)
    except asyncio.TimeoutError:
        raise MandatUnavailable(f"javob {FETCH_DEADLINE}s ichida kelmadi")


async def _fetch_and_store(abt_id: str) -> dict | None:
    """Saytdan olish + natijani saqlash — bitta ajralmas fon vazifa.

    Kutuvchilarning hammasi deadline bilan ketib qolsa ham bu vazifa oxirigacha
    ishlaydi va topilgan yakuniy natijani omborga yozadi.
    """
    info = await fetch_details(abt_id)
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
    try:
        await redis.set(FINAL_PREFIX + info["abt_id"], json.dumps(info), ex=FINAL_TTL)
    except Exception as e:
        logging.warning(f"Redis'ga yakuniy natijani yozib bo'lmadi: {e}")
