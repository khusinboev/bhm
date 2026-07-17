"""Tarqatishni boshqarish — yengil qatlam.

Tarqatishning O'ZI botdan mustaqil jarayonda ishlaydi (tarqat_worker.py):
bot uni faqat ishga tushiradi va Redis orqali kuzatadi/to'xtatadi.
Bot qayta ishga tushsa ham tarqatish davom etaveradi.

  /tarqat       — mustaqil worker jarayonini ishga tushirish
  /tarqat_holat — jarayon holati (Redis'dagi jonli statistika)
  /tarqat_stop  — to'xtatish bayrog'ini qo'yish (worker o'zi ko'rib to'xtaydi)
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import redis.asyncio as aioredis
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_ID

tarqat_router = Router()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKER_PATH = PROJECT_ROOT / "tarqat_worker.py"
WORKER_LOG = PROJECT_ROOT / "tarqat.log"

LOCK_KEY = "tarqat:lock"
STATUS_KEY = "tarqat:status"
STOP_KEY = "tarqat:stop"

redis = aioredis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


def _holat_text(raw: str | None) -> str:
    if not raw:
        return "Hali tarqatish boshlanmagan (yoki holat ma'lumoti eskirgan)."
    try:
        s = json.loads(raw)
    except ValueError:
        return "Holat ma'lumotini o'qib bo'lmadi."
    if s.get("toxtatildi"):
        holat = "to'xtatilgan ⛔"
    elif s.get("tugadi"):
        holat = "tugagan ✅"
    else:
        holat = "davom etmoqda ⏳"
    return (
        f"📦 <b>Tarqatish holati</b> ({s.get('boshlandi')} da boshlangan, {holat})\n"
        f"⚙️ Jarayon: mustaqil, PID {s.get('pid')}\n\n"
        f"👥 Userlar: {s.get('korildi_user')}/{s.get('jami_user')} ko'rildi\n"
        f"📨 Xabar yuborildi: {s.get('yuborildi_user')} ta userga\n"
        f"🆔 Belgilandi: {s.get('belgilandi_id')} ta ID (jami {s.get('jami_id')} ta)\n"
        f"⏭ Hozircha natijasiz: {s.get('natijasiz_id')} ta ID\n"
        f"🚫 Botni bloklagan: {s.get('bloklagan')}\n"
        f"⚠️ Xatolar: {s.get('xato')}"
    )


@tarqat_router.message(Command("tarqat"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_start(message: Message):
    # Bitta nusxa: lock'ni "starting" bilan band qilamiz — worker o'z PID'i
    # bilan egallab oladi. Lock band bo'lsa — allaqachon ishlayapti.
    ok = await redis.set(LOCK_KEY, "starting", nx=True, ex=60)
    if not ok:
        raw = await redis.get(STATUS_KEY)
        await message.answer("⏳ Tarqatish allaqachon ketmoqda.\n\n" + _holat_text(raw),
                             parse_mode="html")
        return

    await redis.delete(STOP_KEY)
    try:
        with open(WORKER_LOG, "a") as log_file:
            subprocess.Popen(
                [sys.executable, str(WORKER_PATH)],
                cwd=str(PROJECT_ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # bot to'xtasa ham worker yashaydi
            )
    except Exception as e:
        logging.exception("Worker jarayonini ishga tushirib bo'lmadi")
        await redis.delete(LOCK_KEY)
        await message.answer(f"🚨 Tarqatishni ishga tushirib bo'lmadi: {e}")
        return

    await message.answer(
        "🚀 Tarqatish mustaqil jarayon sifatida ishga tushirildi.\n"
        "U botdan alohida ishlaydi — bot qayta ishga tushsa ham davom etadi.\n\n"
        "Holat: /tarqat_holat\nTo'xtatish: /tarqat_stop"
    )


@tarqat_router.message(Command("tarqat_holat"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_status(message: Message):
    raw = await redis.get(STATUS_KEY)
    lock = await redis.get(LOCK_KEY)
    text = _holat_text(raw)
    if lock is None and raw:
        text += "\n\nℹ️ Jarayon hozir faol emas (lock bo'shatilgan)."
    await message.answer(text, parse_mode="html")


@tarqat_router.message(Command("tarqat_stop"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_stop(message: Message):
    lock = await redis.get(LOCK_KEY)
    if lock is None:
        await message.answer("Hozir faol tarqatish yo'q.")
        return
    await redis.set(STOP_KEY, "1", ex=3600)
    await message.answer("⛔ To'xtatish so'raldi — worker joriy userni tugatib to'xtaydi.\n"
                         "Yakuniy hisobot workerdan keladi.")
