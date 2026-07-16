"""Yakuniy natijalarni buyurtma egalariga tarqatish.

Admin buyruqlari:
  /tarqat       — tarqatishni boshlash (fonda ishlaydi, bot bloklanmaydi)
  /tarqat_holat — jarayon holati
  /tarqat_stop  — to'xtatish

Tarqatish user oqimiga xalaqit bermasligi uchun:
  - natija avval ombordan (natijalar jadvali) olinadi — saytga so'rov
    faqat hali topilmagan ID'lar uchun ketadi;
  - sayt navbatida userlarning so'rovlari ko'payib qolsa (PRESSURE_LIMIT),
    tarqatish o'zi to'xtab, navbat bo'shashini kutadi;
  - sayt band/javobsiz bo'lsa uzun sleep bilan qayta uriniladi, bo'lmasa
    o'sha ID keyingi /tarqat ga qoldiriladi;
  - xabarlar orasida pauza bor — Telegram flood limitiga urilmaydi.

Ball topilgan ID notified_at bilan belgilanadi va boshqa qayta izlanmaydi.
Hech narsasi topilmagan userga hech narsa yuborilmaydi.
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

from config import bot, ADMIN_ID
from src.db import database
from src.utils import result_service
from src.utils.mandat_parser import MandatBusy, MandatUnavailable, pending_count

tarqat_router = Router()

MAX_IDS_PER_USER = 3   # bir buyruqda bir userdan tekshiriladigan oxirgi ID'lar
PRESSURE_LIMIT = 25    # user navbati shundan oshsa tarqatish yo'l beradi
PRESSURE_SLEEP = 15    # navbat bo'shashini kutish (soniya)
FETCH_RETRIES = 3      # sayt band/javobsiz bo'lsa urinishlar soni
SEND_DELAY = 0.3       # xabarlar orasidagi pauza (soniya)

_task: asyncio.Task | None = None
_st: dict = {}


def _new_stats() -> dict:
    return {
        "boshlandi": datetime.now().strftime("%d.%m %H:%M:%S"),
        "jami_user": 0, "jami_id": 0,
        "korildi_user": 0, "yuborildi_user": 0,
        "belgilandi_id": 0, "natijasiz_id": 0,
        "bloklagan": 0, "xato": 0,
        "tugadi": False,
    }


def _holat_text() -> str:
    if not _st:
        return "Hali tarqatish boshlanmagan."
    s = _st
    holat = "tugagan ✅" if s["tugadi"] else "davom etmoqda ⏳"
    return (
        f"📦 <b>Tarqatish holati</b> ({s['boshlandi']} da boshlangan, {holat})\n\n"
        f"👥 Userlar: {s['korildi_user']}/{s['jami_user']} ko'rildi\n"
        f"📨 Xabar yuborildi: {s['yuborildi_user']} ta userga\n"
        f"🆔 Belgilandi: {s['belgilandi_id']} ta ID (jami {s['jami_id']} ta tekshirilmoqda)\n"
        f"⏭ Hozircha natijasiz: {s['natijasiz_id']} ta ID\n"
        f"🚫 Botni bloklagan: {s['bloklagan']}\n"
        f"⚠️ Xatolar: {s['xato']}"
    )


async def _resolve(abt_id: str) -> dict | None:
    """Natijani oladi. None — natija yo'q yoki hozircha olib bo'lmadi.

    Sayt band bo'lsa userlarga yo'l berib kutadi; baribir bo'lmasa ID
    keyingi tarqatishga qoladi (belgilanmaydi).
    """
    for _ in range(FETCH_RETRIES):
        while pending_count() > PRESSURE_LIMIT:
            await asyncio.sleep(PRESSURE_SLEEP)
        try:
            return await result_service.get_result(abt_id)
        except MandatBusy:
            await asyncio.sleep(30)
        except MandatUnavailable:
            await asyncio.sleep(15)
        except Exception:
            logging.exception(f"Tarqatish: {abt_id} bo'yicha kutilmagan xato")
            return None
    return None


def _block(n: int, info: dict) -> str:
    """Bitta ID uchun ixcham natija bloki."""
    lines = [f"{n}) 🆔 <b>{info['abt_id']}</b> — {info.get('fio') or ''}"]
    labels = ["Majburiy fanlar", "1-mutaxassislik fani", "2-mutaxassislik fani"]
    fanlar = info.get("fanlar") or []
    for i, item in enumerate(fanlar[:3]):
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
    tail = (
        "\n\n📊 To'liq ma'lumotni (javoblar varaqasi bilan) botdagi "
        "\"📊 Natija\" bo'limida ko'rishingiz mumkin.\n"
        "<b>✔️ Buyurtma @mandat_uzbmbbot tomonidan bajarildi.</b>"
    )
    text = head + "\n\n".join(blocks) + tail
    if len(text) > 4000:
        # Juda uzun bo'lsa — faqat FIO va umumiy ball qatorlari
        minimal = ["\n".join((b.splitlines()[0], b.splitlines()[-1])) for b in blocks]
        text = head + "\n\n".join(minimal) + tail
    return text


async def _send_to_user(user_id: int, text: str) -> str:
    """Natija: 'sent' | 'blocked' | 'failed'."""
    try:
        await bot.send_message(user_id, text, parse_mode="html")
        return "sent"
    except TelegramRetryAfter as e:
        await asyncio.sleep(min(e.retry_after + 1, 60))
        try:
            await bot.send_message(user_id, text, parse_mode="html")
            return "sent"
        except Exception:
            return "failed"
    except TelegramForbiddenError:
        return "blocked"
    except Exception:
        logging.exception(f"Tarqatish: {user_id} ga yuborib bo'lmadi")
        return "failed"


async def _run(admin_id: int) -> None:
    global _task
    st = _st
    try:
        # Oy boshidan beri, hali yuborilmagan buyurtmalar — har userdan oxirgi 3 tasi
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

        st["jami_user"] = len(users)
        st["jami_id"] = len(rows)

        if not users:
            st["tugadi"] = True
            await bot.send_message(admin_id, "📭 Tarqatish uchun buyurtma topilmadi.")
            return

        await bot.send_message(
            admin_id,
            f"🚀 Tarqatish boshlandi: {len(users)} ta user, {len(rows)} ta ID tekshiriladi."
        )

        for user_id, id_list in users.items():
            st["korildi_user"] += 1
            try:
                blocks: list[str] = []
                done_rows: list[int] = []
                for abt_id, row_id in id_list:
                    info = await _resolve(abt_id)
                    if info is not None and result_service.is_final(info):
                        blocks.append(_block(len(blocks) + 1, info))
                        done_rows.append(row_id)
                    else:
                        st["natijasiz_id"] += 1

                if not blocks:
                    continue  # hech narsa topilmadi — xabar yuborilmaydi

                outcome = await _send_to_user(user_id, _compose(blocks))
                if outcome == "sent":
                    st["yuborildi_user"] += 1
                elif outcome == "blocked":
                    st["bloklagan"] += 1
                else:
                    st["xato"] += 1

                # Yuborilgan (yoki bloklagan) userning topilgan ID'lari belgilanadi —
                # ular bo'yicha boshqa izlanmaydi. Xato bo'lsa keyingi safarga qoladi.
                if outcome in ("sent", "blocked"):
                    await database.execute(
                        "UPDATE bhm SET notified_at = NOW() WHERE id = ANY(%s)",
                        (done_rows,)
                    )
                    st["belgilandi_id"] += len(done_rows)

                await asyncio.sleep(SEND_DELAY)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(f"Tarqatish: user {user_id} blokida xato")
                st["xato"] += 1

        st["tugadi"] = True
        await bot.send_message(admin_id, "✅ Tarqatish yakunlandi!\n\n" + _holat_text(),
                               parse_mode="html")
    except asyncio.CancelledError:
        try:
            await bot.send_message(admin_id, "⛔ Tarqatish to'xtatildi.\n\n" + _holat_text(),
                                   parse_mode="html")
        except Exception:
            pass
        raise
    except Exception as e:
        logging.exception("Tarqatishda kutilmagan xato")
        try:
            await bot.send_message(admin_id, f"🚨 Tarqatish xato bilan to'xtadi: {e}")
        except Exception:
            pass
    finally:
        _task = None


@tarqat_router.message(Command("tarqat"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_start(message: Message):
    global _task
    if _task is not None and not _task.done():
        await message.answer("⏳ Tarqatish allaqachon ketmoqda.\n\n" + _holat_text(),
                             parse_mode="html")
        return
    _st.clear()
    _st.update(_new_stats())
    _task = asyncio.create_task(_run(message.from_user.id))
    await message.answer(
        "🚀 Tarqatish fonda boshlandi.\n"
        "Holat: /tarqat_holat\nTo'xtatish: /tarqat_stop"
    )


@tarqat_router.message(Command("tarqat_holat"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_status(message: Message):
    await message.answer(_holat_text(), parse_mode="html")


@tarqat_router.message(Command("tarqat_stop"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def tarqat_stop(message: Message):
    global _task
    if _task is None or _task.done():
        await message.answer("Hozir faol tarqatish yo'q.")
        return
    _task.cancel()
    await message.answer("⛔ To'xtatish so'raldi — yakuniy holat birozdan so'ng keladi.")
