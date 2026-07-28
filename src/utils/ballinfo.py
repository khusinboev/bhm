"""'Balingizga mos yo'nalish' — mandat.uzbmb.uz/Bakalavr/BallInfoByResult.

Sayt JSON API beradi: GET /Bakalavr/BallInfoByResultJson?entrantId=<7 xonali>
Javob: {success, message?, status?('absent'|'banned'|'below'), belowThreshold?,
        data:{fullName, edlang, result, details:[{regionName, universityName,
        educLanguage, facultyName, ballK, ...}]}}
details — abituriyent fan majmuasidagi BARCHA yo'nalishlar (500+ bo'lishi
mumkin), ballK — o'tish balli; ballK <= result bo'lsa "ball yetadi".

Sayt bilan ishlash strategiyasi natija bo'limiga o'xshash, lekin ALOHIDA
hisoblagichlar bilan (ikkala bo'lim bir-birini navbatda siqib qo'ymaydi):
  - o'z semaphore (4) va navbat chegarasi (15) — saytga o'ta ehtiyotkor;
  - bir xil ID birlashtiriladi, shield bilan himoyalanadi;
  - har chaqiruvchining kutish chegarasi 20s;
  - natija Postgres (yonalishlar jadvali) + Redis'da saqlanadi:
    o'tish ballari mandat davomida o'zgarib turadi, shu sababli saqlangan
    ma'lumot FRESH_TTL'dan eskirsa yangilanadi (yangilash muvaffaqiyatsiz
    bo'lsa eski nusxa ko'rsatilaveradi).
"""

import asyncio
import json
import logging
from datetime import datetime

import aiohttp
import redis.asyncio as aioredis

from config import REDIS_DB
from src.db import database
from src.utils.mandat_parser import USER_AGENT, MandatBusy, MandatUnavailable

BALLINFO_URL = "https://mandat.uzbmb.uz/Bakalavr/BallInfoByResultJson"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=15)
RETRY_COUNT = 2

# Natija bo'limidan ALOHIDA chegaralar — bu bo'lim o'ta ehtiyotkor
semaphore = asyncio.Semaphore(4)
MAX_QUEUE = 15
_waiting = 0
_inflight: dict[str, asyncio.Task] = {}

FETCH_DEADLINE = 20   # har chaqiruvchining kutish chegarasi (soniya)
FRESH_TTL = 6 * 3600  # saqlangan snapshot shu muddatgacha "yangi" hisoblanadi
TEXT_TTL = 2 * 3600   # tayyor xabar matni Redis keshi
NEG_TTL = 30 * 60     # salbiy holatlar (below/absent/banned) keshi
NOTFOUND_TTL = 10 * 60

TEXT_PREFIX = "mandat:bi:text:"
NEG_PREFIX = "mandat:bi:neg:"

redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                )
    return _session


async def close_session() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


# ============ Saytdan olish (himoyalangan) ============

def _release_slot(_task: asyncio.Task, abt_id: str) -> None:
    global _waiting
    _waiting -= 1
    _inflight.pop(abt_id, None)


async def _fetch(abt_id: str) -> dict:
    """Saytdan xom JSON javob. MandatUnavailable — sayt javob bermadi."""
    session = await _get_session()
    last_err: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            async with semaphore:
                async with session.get(BALLINFO_URL, params={"entrantId": abt_id}) as resp:
                    return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            last_err = e
            logging.warning(f"BallInfo so'rovi muvaffaqiyatsiz ({attempt}-urinish, ID={abt_id}): {e}")
            if attempt < RETRY_COUNT:
                await asyncio.sleep(2)
    raise MandatUnavailable(str(last_err))


async def _fetch_and_store(abt_id: str) -> str:
    """Saytdan olish + saqlash + formatlash — bitta ajralmas fon vazifa.

    Qaytaradi: userga yuboriladigan tayyor matn.
    """
    raw = await _fetch(abt_id)

    if not raw.get("success"):
        text = ("❌ Bunday ID topilmadi. Iltimos, ID raqamini tekshiring.\n\n"
                "<i>Mandat saytidagi uzilishlar sababli ham topilmayotgan bo'lishi mumkin — "
                "birozdan so'ng qayta urinib ko'ring.</i>")
        await _cache_set(NEG_PREFIX + abt_id, text, NOTFOUND_TTL)
        return text

    data = raw.get("data") or {}
    status = raw.get("status") or ("below" if raw.get("belowThreshold") else "ok")
    details = data.get("details") or []

    if status != "ok" or not details:
        text = _format_negative(abt_id, data, status)
        await _cache_set(NEG_PREFIX + abt_id, text, NEG_TTL)
        return text

    # Muvaffaqiyatli natija — Postgres'ga snapshot yoziladi
    try:
        await database.execute(
            """
            INSERT INTO yonalishlar (abt_id, fio, ball, result_json, found_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (abt_id) DO UPDATE
                SET fio = EXCLUDED.fio, ball = EXCLUDED.ball,
                    result_json = EXCLUDED.result_json, found_at = NOW()
            """,
            (abt_id, data.get("fullName"), data.get("result"), json.dumps(data)),
        )
    except Exception:
        logging.exception(f"Yo'nalishlar snapshotini saqlab bo'lmadi (ID={abt_id})")

    text = format_report(abt_id, data)
    await _cache_set(TEXT_PREFIX + abt_id, text, TEXT_TTL)
    return text


async def _cache_set(key: str, value: str, ttl: int) -> None:
    try:
        await redis.set(key, value, ex=ttl)
    except Exception as e:
        logging.warning(f"Redis yozish xatosi: {e}")


async def _cache_get(key: str) -> str | None:
    try:
        return await redis.get(key)
    except Exception as e:
        logging.warning(f"Redis o'qish xatosi: {e}")
        return None


async def get_report(abt_id: str) -> str:
    """Userga yuboriladigan tayyor matn. Tartib: Redis -> Postgres(yangi) -> sayt.

    MandatBusy — navbat to'la; MandatUnavailable — sayt javob bermadi
    (lekin eskirgan snapshot bo'lsa, xato o'rniga o'sha qaytariladi).
    """
    global _waiting

    cached = await _cache_get(TEXT_PREFIX + abt_id)
    if cached:
        return cached
    cached = await _cache_get(NEG_PREFIX + abt_id)
    if cached:
        return cached

    # Postgres snapshot — yangi bo'lsa shundan foydalanamiz
    stale_row = None
    try:
        row = await database.fetchone(
            """SELECT result_json, found_at >= NOW() - make_interval(secs => %s)
               FROM yonalishlar WHERE abt_id = %s""",
            (FRESH_TTL, abt_id),
        )
        if row:
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            if row[1]:  # hali yangi
                text = format_report(abt_id, data)
                await _cache_set(TEXT_PREFIX + abt_id, text, TEXT_TTL)
                return text
            stale_row = data  # eskirgan — yangilashga urinamiz, bo'lmasa shu qoladi
    except Exception:
        logging.exception(f"Yo'nalishlar snapshotini o'qib bo'lmadi (ID={abt_id})")

    task = _inflight.get(abt_id)
    if task is None:
        if _waiting >= MAX_QUEUE:
            if stale_row is not None:
                return format_report(abt_id, stale_row, stale=True)
            raise MandatBusy()
        _waiting += 1  # tekshiruv bilan bitta sinxron blokda — poyga yo'q
        task = asyncio.create_task(_fetch_and_store(abt_id))
        _inflight[abt_id] = task
        task.add_done_callback(lambda _t, _id=abt_id: _release_slot(_t, _id))

    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=FETCH_DEADLINE)
    except (asyncio.TimeoutError, MandatUnavailable):
        if stale_row is not None:
            # Sayt hozir bermadi — eskiroq snapshot baribir foydali
            return format_report(abt_id, stale_row, stale=True)
        raise MandatUnavailable(f"javob {FETCH_DEADLINE}s ichida kelmadi")


# ============ Ko'rsatish (formatlash) ============

MAX_LEN = 3700  # xabar tanasi uchun xavfsiz chegara (4096 dan pastroq)


def _format_negative(abt_id: str, data: dict, status: str) -> str:
    fio = data.get("fullName") or ""
    ball = data.get("result")
    head = (f"🎯 <b>Balingizga mos yo'nalishlar</b>\n"
            f"━━━━━━━━━━\n"
            f"🪪 {fio}\n🆔 <b>{abt_id}</b>\n\n")
    if status == "absent" or (isinstance(ball, (int, float)) and ball == -2):
        body = "ℹ️ Siz test sinovlarida ishtirok etmagansiz (yoki natijalar hali e'lon qilinmagan)."
    elif status == "banned" or (isinstance(ball, (int, float)) and ball == -1):
        body = "ℹ️ Test natijangiz bekor qilingan."
    elif status == "below":
        body = (f"🎓 Umumiy balingiz: <b>{ball}</b>\n\n"
                "ℹ️ Afsuski, balingiz o'tish uchun belgilangan eng past chegaradan (56.7) "
                "past — hozircha tavsiya qilinadigan yo'nalishlar yo'q.")
    else:
        body = "ℹ️ Fan majmuangiz bo'yicha yo'nalishlar topilmadi."
    tail = "\n\n<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>"
    return head + body + tail


def format_report(abt_id: str, data: dict, stale: bool = False) -> str:
    """557 tagacha yo'nalishni bitta o'qiladigan xabarga jamlaydi.

    Reja: sarlavha (FIO/ball/statistika) -> balingiz YETADIGAN yo'nalishlar,
    o'tish balli yuqoridan pastga (eng nufuzli-yu yetadiganlari birinchi),
    xabar sig'imiga qarab dinamik kesiladi. Yetadigani bo'lmasa — eng yaqin
    (farqi kichik) 10 tasi ko'rsatiladi.
    """
    fio = data.get("fullName") or ""
    ball = data.get("result") or 0
    edlang = data.get("edlang") or ""
    details = data.get("details") or []

    def _ballk(item) -> float:
        try:
            return float(item.get("ballK") or 0)
        except (TypeError, ValueError):
            return 0.0

    passing = sorted((d for d in details if _ballk(d) <= ball), key=_ballk, reverse=True)
    failing = sorted((d for d in details if _ballk(d) > ball), key=_ballk)

    lines = [
        "🎯 <b>Balingizga mos yo'nalishlar</b>",
        "━━━━━━━━━━",
        f"🪪 {fio}",
        f"🆔 <b>{abt_id}</b> | 🗣 {edlang}",
        f"🎓 Umumiy ball: <b>{ball}</b>",
        "",
        f"📚 Fan majmuangizdagi yo'nalishlar: <b>{len(details)}</b> ta",
        f"✅ Balingiz yetadi: <b>{len(passing)}</b> ta",
        "",
    ]

    def _row(i: int, item: dict, extra: str = "") -> str:
        return (f"{i}. <b>{item.get('universityName')}</b>\n"
                f"    {item.get('facultyName')}\n"
                f"    📍 {item.get('regionName')} | {item.get('educLanguage')} | "
                f"o'tish: <b>{_ballk(item):.1f}</b>{extra}")

    if passing:
        lines.append("🏆 <b>Balingiz yetadigan yo'nalishlar</b> (o'tish balli yuqorilari birinchi):")
        body_len = sum(len(x) + 1 for x in lines)
        shown = 0
        for item in passing:
            row = _row(shown + 1, item)
            if body_len + len(row) > MAX_LEN:
                break
            lines.append(row)
            body_len += len(row) + 1
            shown += 1
        if shown < len(passing):
            lines.append(f"\n…va yana <b>{len(passing) - shown}</b> ta yo'nalishga balingiz yetadi.")
    else:
        lines.append("😕 Hozircha balingiz yetadigan yo'nalish yo'q.")
        if failing:
            lines.append("\n📈 <b>Balingizga eng yaqin yo'nalishlar:</b>")
            for i, item in enumerate(failing[:10], 1):
                farq = _ballk(item) - ball
                lines.append(_row(i, item, extra=f" (farq: {farq:.1f})"))

    lines.append("")
    if stale:
        lines.append("⚠️ <i>Sayt hozir javob bermayapti — oldinroq olingan ma'lumot ko'rsatildi.</i>")
    lines.append("ℹ️ To'liq ro'yxat: mandat.uzbmb.uz saytida")
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>")
    return "\n".join(lines)
