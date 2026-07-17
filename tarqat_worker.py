"""Tarqatish — botdan MUSTAQIL jarayon.

Bot jarayonidan butunlay ajratilgan: /tarqat buyrug'i bu skriptni alohida
jarayon sifatida ishga tushiradi, xolos. Bot qayta ishga tushsa/yiqilsa ham
tarqatish davom etaveradi. Xabarlar Telegram Bot API'ga TO'G'RIDAN-TO'G'RI
HTTP so'rovlar bilan yuboriladi (aiogram dispatcher ishlatilmaydi).

Bot bilan aloqa faqat Redis orqali:
  tarqat:lock    — bitta nusxa kafolati (heartbeat bilan yangilanadi)
  tarqat:status  — jonli statistika (bot /tarqat_holat da ko'rsatadi)
  tarqat:stop    — to'xtatish bayrog'i (bot /tarqat_stop da qo'yadi)

Qo'lda ishga tushirish: <venv>/bin/python tarqat_worker.py
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime

import aiohttp
import redis.asyncio as aioredis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import BOT_TOKEN, ADMIN_ID, REDIS_DB  # noqa: E402  (.env shu yerda o'qiladi)
from src.db import database  # noqa: E402
from src.utils import result_service  # noqa: E402
from src.utils.mandat_parser import MandatBusy, MandatUnavailable, close_session  # noqa: E402

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

LOCK_KEY = "tarqat:lock"
STATUS_KEY = "tarqat:status"
STOP_KEY = "tarqat:stop"
LOCK_TTL = 120          # heartbeat har userda yangilab boradi

MAX_IDS_PER_USER = 3    # bir yurgizishda bir userdan tekshiriladigan oxirgi ID'lar
SEND_DELAY = 0.25       # xabarlar orasidagi pauza (bot limitiga joy qoladi)
SITE_DELAY = 0.7        # saytga ketma-ket so'rovlar orasidagi pauza
FETCH_RETRIES = 3       # sayt band/javobsiz bo'lsa urinishlar

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)

_stop_requested = False


def _handle_sigterm(*_args):
    global _stop_requested
    _stop_requested = True


# ============ Telegram Bot API (to'g'ridan-to'g'ri) ============

async def tg_send(session: aiohttp.ClientSession, chat_id: int, text: str) -> str:
    """sendMessage. Natija: 'sent' | 'blocked' | 'failed'."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    for attempt in (1, 2):
        try:
            async with session.post(f"{API_URL}/sendMessage", json=payload) as resp:
                data = await resp.json()
        except Exception as e:
            logging.warning(f"sendMessage tarmoq xatosi (chat={chat_id}): {e}")
            if attempt == 1:
                await asyncio.sleep(3)
                continue
            return "failed"

        if data.get("ok"):
            return "sent"
        code = data.get("error_code")
        if code == 429:
            retry_after = (data.get("parameters") or {}).get("retry_after", 5)
            logging.warning(f"Flood-wait {retry_after}s (chat={chat_id})")
            await asyncio.sleep(min(retry_after, 60) + 0.5)
            continue
        if code == 403:
            return "blocked"  # user botni bloklagan
        logging.warning(f"sendMessage rad etildi (chat={chat_id}): {data.get('description')}")
        return "failed"
    return "failed"


# ============ Xabar matni (ixcham, bitta xabar) ============

def _block(n: int, info: dict) -> str:
    lines = [f"{n}) 🆔 <b>{info['abt_id']}</b> — {info.get('fio') or ''}"]
    labels = ["Majburiy fanlar", "1-mutaxassislik fani", "2-mutaxassislik fani"]
    for i, item in enumerate((info.get("fanlar") or [])[:3]):
        if not item or len(item) < 2:
            continue
        lbl = labels[i] if i < len(labels) else f"{i + 1}-fan"
        lines.append(f"    {i + 1}️⃣ {lbl}: {item[0]} ta to'g'ri — {item[1]} ball")
    for lbl, val in (info.get("scores") or []):
        lines.append(f"    📌 {lbl}: {val}")
    lines.append(f"    ✅ <b>Umumiy ball: {info.get('umumiy_ball')}</b>")
    return "\n".join(lines)


def _compose(blocks: list[str]) -> str:
    head = "🎉 <b>Mandat natija buyurtmangiz tayyor!</b>\n\n"
    tail = ("\n\n📊 To'liq ma'lumotni (javoblar varaqasi bilan) botdagi "
            "\"📊 Natija\" bo'limida ko'rishingiz mumkin.\n"
            "<b>✔️ Buyurtma @mandat_uzbmbbot tomonidan bajarildi.</b>")
    text = head + "\n\n".join(blocks) + tail
    if len(text) > 4000:
        minimal = ["\n".join((b.splitlines()[0], b.splitlines()[-1])) for b in blocks]
        text = head + "\n\n".join(minimal) + tail
    return text


# ============ Holat va boshqaruv ============

def _new_stats() -> dict:
    return {
        "boshlandi": datetime.now().strftime("%d.%m %H:%M:%S"),
        "pid": os.getpid(),
        "jami_user": 0, "jami_id": 0,
        "korildi_user": 0, "yuborildi_user": 0,
        "belgilandi_id": 0, "natijasiz_id": 0,
        "bloklagan": 0, "xato": 0,
        "tugadi": False, "toxtatildi": False,
    }


async def _push_status(stats: dict) -> None:
    try:
        await redis.set(STATUS_KEY, json.dumps(stats), ex=6 * 3600)
        await redis.expire(LOCK_KEY, LOCK_TTL)  # heartbeat
    except Exception as e:
        logging.warning(f"Statusni yozib bo'lmadi: {e}")


async def _should_stop() -> bool:
    if _stop_requested:
        return True
    try:
        return bool(await redis.get(STOP_KEY))
    except Exception:
        return False


async def _resolve(abt_id: str) -> dict | None:
    """Natijani oladi (ombor -> kesh -> sayt). Olib bo'lmasa None (keyingi safarga)."""
    for _ in range(FETCH_RETRIES):
        try:
            return await result_service.get_result(abt_id)
        except MandatBusy:
            await asyncio.sleep(30)
        except MandatUnavailable:
            await asyncio.sleep(15)
        except Exception:
            logging.exception(f"{abt_id} bo'yicha kutilmagan xato")
            return None
    return None


# ============ Asosiy jarayon ============

async def run() -> None:
    # Bitta nusxa kafolati: bot "starting" qiymati bilan oldindan band qilgan
    # bo'lishi mumkin — uni o'z PID'imiz bilan egallab olamiz
    ok = await redis.set(LOCK_KEY, str(os.getpid()), nx=True, ex=LOCK_TTL)
    if not ok:
        current = await redis.get(LOCK_KEY)
        if current == "starting":
            await redis.set(LOCK_KEY, str(os.getpid()), ex=LOCK_TTL)
        else:
            logging.error(f"Boshqa tarqatish jarayoni ishlayapti (lock={current}) — chiqilyapti")
            return

    stats = _new_stats()
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    try:
        await redis.delete(STOP_KEY)

        rows = await database.fetchall("""
            SELECT user_id, abt_id, id FROM (
                SELECT user_id, abt_id, id,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn
                FROM bhm
                WHERE created_at >= date_trunc('month', NOW())
                  AND notified_at IS NULL
            ) t
            WHERE rn <= %s
            ORDER BY user_id, id
        """, (MAX_IDS_PER_USER,))

        users: dict[int, list[tuple[str, int]]] = {}
        for user_id, abt_id, row_id in rows:
            users.setdefault(user_id, []).append((abt_id, row_id))

        stats["jami_user"] = len(users)
        stats["jami_id"] = len(rows)
        await _push_status(stats)

        for admin in ADMIN_ID[:1]:
            await tg_send(session, admin,
                          f"🚀 Tarqatish boshlandi (mustaqil jarayon, PID {os.getpid()}): "
                          f"{len(users)} ta user, {len(rows)} ta ID.")

        if not users:
            stats["tugadi"] = True
            await _push_status(stats)
            return

        for user_id, id_list in users.items():
            if await _should_stop():
                stats["toxtatildi"] = True
                logging.info("To'xtatish so'raldi — jarayon yakunlanmoqda")
                break

            stats["korildi_user"] += 1
            try:
                blocks: list[str] = []
                done_rows: list[int] = []
                for abt_id, row_id in id_list:
                    info = await _resolve(abt_id)
                    if info is not None and result_service.is_final(info):
                        blocks.append(_block(len(blocks) + 1, info))
                        done_rows.append(row_id)
                    else:
                        stats["natijasiz_id"] += 1
                    await asyncio.sleep(SITE_DELAY)

                if not blocks:
                    continue  # hech narsa topilmadi — xabar yuborilmaydi

                outcome = await tg_send(session, user_id, _compose(blocks))
                if outcome == "sent":
                    stats["yuborildi_user"] += 1
                elif outcome == "blocked":
                    stats["bloklagan"] += 1
                else:
                    stats["xato"] += 1

                if outcome in ("sent", "blocked"):
                    await database.execute(
                        "UPDATE bhm SET notified_at = NOW() WHERE id = ANY(%s)",
                        (done_rows,)
                    )
                    stats["belgilandi_id"] += len(done_rows)

                await asyncio.sleep(SEND_DELAY)
            except Exception:
                logging.exception(f"User {user_id} blokida xato")
                stats["xato"] += 1

            if stats["korildi_user"] % 20 == 0:
                await _push_status(stats)

        stats["tugadi"] = True
        await _push_status(stats)

        yakun = "⛔ Tarqatish to'xtatildi" if stats["toxtatildi"] else "✅ Tarqatish yakunlandi"
        for admin in ADMIN_ID[:1]:
            await tg_send(session, admin,
                          f"{yakun}!\n\n"
                          f"👥 Userlar: {stats['korildi_user']}/{stats['jami_user']}\n"
                          f"📨 Yuborildi: {stats['yuborildi_user']}\n"
                          f"🆔 Belgilandi: {stats['belgilandi_id']} ta ID\n"
                          f"⏭ Natijasiz: {stats['natijasiz_id']}\n"
                          f"🚫 Bloklagan: {stats['bloklagan']}\n"
                          f"⚠️ Xatolar: {stats['xato']}")
    except Exception:
        logging.exception("Tarqatishda halokatli xato")
        stats["tugadi"] = True
        await _push_status(stats)
    finally:
        try:
            await redis.delete(LOCK_KEY, STOP_KEY)
        except Exception:
            pass
        await session.close()
        await close_session()
        await database.close_pool()
        await redis.aclose()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    asyncio.run(run())
