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
import io
import json
import logging
import os
from datetime import datetime
from typing import Any

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
NEG_TTL = 30 * 60     # salbiy holatlar (below/absent/banned) keshi
NOTFOUND_TTL = 10 * 60

NEG_PREFIX = "mandat:bi:neg:"

PER_PAGE = 10  # bir sahifada ko'rsatiladigan yo'nalishlar soni

FILTER_KEYS = ("region", "university", "faculty")
FILTER_LABELS = {
    "region": "Hudud",
    "university": "OTM",
    "faculty": "Yo'nalish",
}

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


async def _fetch_and_store(abt_id: str) -> dict:
    """Saytdan olish + saqlash — bitta ajralmas fon vazifa.

    Qaytaradi: {"text": str}  — salbiy holat (tayyor xabar), yoki
               {"data": dict} — sahifalab ko'rsatiladigan to'liq ma'lumot.
    """
    raw = await _fetch(abt_id)

    if not raw.get("success"):
        text = ("❌ Bunday ID topilmadi. Iltimos, ID raqamini tekshiring.\n\n"
                "<i>Mandat saytidagi uzilishlar sababli ham topilmayotgan bo'lishi mumkin — "
                "birozdan so'ng qayta urinib ko'ring.</i>")
        await _cache_set(NEG_PREFIX + abt_id, text, NOTFOUND_TTL)
        return {"text": text}

    data = raw.get("data") or {}
    status = raw.get("status") or ("below" if raw.get("belowThreshold") else "ok")
    details = data.get("details") or []

    if status != "ok" or not details:
        text = _format_negative(abt_id, data, status)
        await _cache_set(NEG_PREFIX + abt_id, text, NEG_TTL)
        return {"text": text}

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

    return {"data": data}


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


async def get_data(abt_id: str) -> dict:
    """Sahifalash uchun ma'lumot. Tartib: Redis(neg) -> Postgres(yangi) -> sayt.

    Qaytaradi: {"text": str} — tayyor xabar (salbiy holat), yoki
               {"data": dict, "stale": bool} — sahifalanadigan ma'lumot.
    MandatBusy — navbat to'la; MandatUnavailable — sayt javob bermadi
    (lekin eskirgan snapshot bo'lsa, xato o'rniga o'sha qaytariladi).
    """
    global _waiting

    cached = await _cache_get(NEG_PREFIX + abt_id)
    if cached:
        return {"text": cached}

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
                return {"data": data, "stale": False}
            stale_row = data  # eskirgan — yangilashga urinamiz, bo'lmasa shu qoladi
    except Exception:
        logging.exception(f"Yo'nalishlar snapshotini o'qib bo'lmadi (ID={abt_id})")

    task = _inflight.get(abt_id)
    if task is None:
        if _waiting >= MAX_QUEUE:
            if stale_row is not None:
                return {"data": stale_row, "stale": True}
            raise MandatBusy()
        _waiting += 1  # tekshiruv bilan bitta sinxron blokda — poyga yo'q
        task = asyncio.create_task(_fetch_and_store(abt_id))
        _inflight[abt_id] = task
        task.add_done_callback(lambda _t, _id=abt_id: _release_slot(_t, _id))

    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=FETCH_DEADLINE)
        if "data" in result:
            return {"data": result["data"], "stale": False}
        return result
    except (asyncio.TimeoutError, MandatUnavailable):
        if stale_row is not None:
            # Sayt hozir bermadi — eskiroq snapshot baribir foydali
            return {"data": stale_row, "stale": True}
        raise MandatUnavailable(f"javob {FETCH_DEADLINE}s ichida kelmadi")


# ============ Ko'rsatish (formatlash, sahifalab) ============


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


def _ballk(item) -> float:
    try:
        return float(item.get("ballK") or 0)
    except (TypeError, ValueError):
        return 0.0


def _row(i: int, item: dict, extra: str = "") -> str:
    return (f"{i}. <b>{item.get('universityName')}</b>\n"
            f"    {item.get('facultyName')}\n"
            f"    📍 {item.get('regionName')} | {item.get('educLanguage')} | "
            f"o'tish: <b>{_ballk(item):.1f}</b>{extra}")


def _id_as_str(val: Any) -> str:
    if val is None:
        return "0"
    return str(val)


def _normalize_filters(data: dict, filters: dict[str, str] | None) -> dict[str, str]:
    details = data.get("details") or []
    src = filters or {}
    normalized = {
        "region": _id_as_str(src.get("region", "0")),
        "university": _id_as_str(src.get("university", "0")),
        "faculty": _id_as_str(src.get("faculty", "0")),
    }

    region_ids = {_id_as_str(d.get("regionId")) for d in details if d.get("regionId") is not None}
    if normalized["region"] != "0" and normalized["region"] not in region_ids:
        normalized["region"] = "0"
        normalized["university"] = "0"
        normalized["faculty"] = "0"

    uni_scope = [d for d in details if normalized["region"] == "0"
                 or _id_as_str(d.get("regionId")) == normalized["region"]]
    uni_ids = {_id_as_str(d.get("universityId")) for d in uni_scope if d.get("universityId") is not None}
    if normalized["university"] != "0" and normalized["university"] not in uni_ids:
        normalized["university"] = "0"
        normalized["faculty"] = "0"

    fac_scope = [d for d in uni_scope if normalized["university"] == "0"
                 or _id_as_str(d.get("universityId")) == normalized["university"]]
    fac_ids = {_id_as_str(d.get("facultyId")) for d in fac_scope if d.get("facultyId") is not None}
    if normalized["faculty"] != "0" and normalized["faculty"] not in fac_ids:
        normalized["faculty"] = "0"

    return normalized


def _apply_filters(details: list[dict], filters: dict[str, str]) -> list[dict]:
    region = filters["region"]
    uni = filters["university"]
    fac = filters["faculty"]
    return [
        d for d in details
        if (region == "0" or _id_as_str(d.get("regionId")) == region)
        and (uni == "0" or _id_as_str(d.get("universityId")) == uni)
        and (fac == "0" or _id_as_str(d.get("facultyId")) == fac)
    ]


def get_filter_options(data: dict, filters: dict[str, str] | None,
                       kind: str) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Saytdagi mantiqqa yaqin kaskadli variantlar: Hudud -> OTM -> Yo'nalish."""
    if kind not in FILTER_KEYS:
        return [], _normalize_filters(data, filters)

    details = data.get("details") or []
    normalized = _normalize_filters(data, filters)
    options: list[tuple[str, str]] = [("0", "Barchasi")]

    if kind == "region":
        rows = {( _id_as_str(d.get("regionId")), (d.get("regionName") or "Noma'lum hudud"))
                for d in details if d.get("regionId") is not None}
        options.extend(sorted(rows, key=lambda x: x[1]))
        return options, normalized

    region_scope = [d for d in details if normalized["region"] == "0"
                    or _id_as_str(d.get("regionId")) == normalized["region"]]
    if kind == "university":
        rows = {( _id_as_str(d.get("universityId")),
                  (d.get("universityName") or "Noma'lum OTM"))
                for d in region_scope if d.get("universityId") is not None}
        options.extend(sorted(rows, key=lambda x: x[1]))
        return options, normalized

    uni_scope = [d for d in region_scope if normalized["university"] == "0"
                 or _id_as_str(d.get("universityId")) == normalized["university"]]
    rows = {( _id_as_str(d.get("facultyId")),
              (d.get("facultyName") or "Noma'lum yo'nalish"))
            for d in uni_scope if d.get("facultyId") is not None}
    options.extend(sorted(rows, key=lambda x: x[1]))
    return options, normalized


def filter_caption(data: dict, filters: dict[str, str] | None) -> tuple[str, dict[str, str]]:
    normalized = _normalize_filters(data, filters)
    parts = []
    for kind in FILTER_KEYS:
        opts, normalized = get_filter_options(data, normalized, kind)
        val = normalized[kind]
        if val == "0":
            parts.append(f"{FILTER_LABELS[kind]}: barchasi")
            continue
        name = next((label for value, label in opts if value == val), "tanlanmagan")
        parts.append(f"{FILTER_LABELS[kind]}: {name}")
    return " | ".join(parts), normalized


def format_page(abt_id: str, data: dict, page: int = 1,
                stale: bool = False,
                filters: dict[str, str] | None = None) -> tuple[str, int, dict[str, str]]:
    """Bitta sahifa matni + jami sahifalar soni + normalizatsiya qilingan filtrlar."""
    fio = data.get("fullName") or ""
    ball = float(data.get("result") or 0)
    edlang = data.get("edlang") or ""
    details = data.get("details") or []

    caption, normalized = filter_caption(data, filters)
    filtered = _apply_filters(details, normalized)
    passing = sorted((d for d in filtered if _ballk(d) <= ball), key=_ballk, reverse=True)
    failing = sorted((d for d in filtered if _ballk(d) > ball), key=_ballk)
    ordered = passing + failing

    total_pages = max(1, -(-len(ordered) // PER_PAGE)) if ordered else 1
    page = min(max(1, page), total_pages)

    lines = [
        "🎯 <b>Balingizga mos yo'nalishlar</b>",
        "━━━━━━━━━━",
        f"🪪 {fio}",
        f"🆔 <b>{abt_id}</b> | 🗣 {edlang}",
        f"🎓 Umumiy ball: <b>{ball}</b>",
        "",
        f"📚 Jami yo'nalishlar: <b>{len(details)}</b> ta",
        f"🔎 Filtrlangan yo'nalishlar: <b>{len(filtered)}</b> ta",
        f"✅ Balingiz yetadi: <b>{len(passing)}</b> ta | ❌ Yetmaydi: <b>{len(failing)}</b> ta",
        f"⚙️ <i>{caption}</i>",
        "",
    ]

    if ordered:
        lines.append(f"📄 <b>Natijalar</b> — {page}/{total_pages}-sahifa:")
        start = (page - 1) * PER_PAGE
        for offset, item in enumerate(ordered[start:start + PER_PAGE], start=1):
            row_no = start + offset
            b = _ballk(item)
            if b <= ball:
                extra = " | ✅ yetadi"
            else:
                extra = f" | ❌ yetmaydi (farq: {b - ball:.1f})"
            lines.append(_row(row_no, item, extra=extra))
    else:
        lines.append("😕 Tanlangan filtrlarga mos yo'nalish topilmadi.")

    lines.append("")
    if stale:
        lines.append("⚠️ <i>Sayt hozir javob bermayapti — oldinroq olingan ma'lumot ko'rsatildi.</i>")
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>")
    return "\n".join(lines), total_pages, normalized


def build_pdf_bytes(abt_id: str, data: dict) -> bytes:
    """Filtrlarsiz barcha yo'nalishlar bo'yicha PDF qaytaradi."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    fio = data.get("fullName") or ""
    edlang = data.get("edlang") or ""
    ball = float(data.get("result") or 0)
    details = data.get("details") or []

    passing = sorted((d for d in details if _ballk(d) <= ball), key=_ballk, reverse=True)
    failing = sorted((d for d in details if _ballk(d) > ball), key=_ballk)
    ordered = passing + failing

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    left = 36
    top = h - 40
    y = top
    line_h = 14

    font_name = "Helvetica"
    dejavu_candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )
    for path in dejavu_candidates:
        if os.path.exists(path):
            if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVuSans", path))
            font_name = "DejaVuSans"
            break

    def draw_line(text: str, bold: bool = False) -> None:
        nonlocal y
        if y < 60:
            pdf.showPage()
            y = top
        pdf.setFont(font_name, 11 if bold else 10)
        pdf.drawString(left, y, text[:135])
        y -= line_h

    draw_line("Balingizga mos yo'nalishlar (to'liq ro'yxat)", bold=True)
    draw_line(f"FIO: {fio}")
    draw_line(f"ID: {abt_id} | Til: {edlang}")
    draw_line(f"Umumiy ball: {ball}")
    draw_line(f"Jami yo'nalishlar: {len(details)} | Yetadi: {len(passing)} | Yetmaydi: {len(failing)}")
    draw_line("")

    for i, item in enumerate(ordered, start=1):
        b = _ballk(item)
        status = "YETADI" if b <= ball else f"YETMAYDI (+{(b - ball):.1f})"
        draw_line(f"{i}. {item.get('universityName') or '-'}", bold=True)
        draw_line(f"   Yo'nalish: {item.get('facultyName') or '-'}")
        draw_line(f"   Hudud: {item.get('regionName') or '-'} | Til: {item.get('educLanguage') or '-'}")
        draw_line(f"   O'tish bali: {b:.1f} | Holat: {status}")
        draw_line("")

    draw_line("Ma'lumotlar @mandat_uzbmbbot tomonidan olindi")
    draw_line(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    pdf.save()
    return buf.getvalue()
