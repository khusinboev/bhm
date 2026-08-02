"""'🏅 O'rin aniqlash' — abituriyentning reytingdagi o'rni va statistika.

Sayt ID bo'yicha qidiruvda abituriyentni o'z fan majmuasidagi to'liq reyting
ro'yxatining aynan o'sha sahifasida ko'rsatadi. Shundan o'rin chiqadi:

    o'rin = (sahifa - 1) * pageSize + sahifadagi pozitsiya

Ro'yxat ball bo'yicha kamayish tartibida saralangani uchun kombinatsiya
bo'yicha agregatlar (jami, 189+, 56.7 dan past, ball darajalari) IKKILIK
QIDIRUV bilan ~12 so'rovda topiladi. Ular kombinatsiyaga umumiy bo'lgani
uchun bir marta hisoblanib bazaga yoziladi va hamma foydalanuvchiga xizmat
qiladi (91 ta kombinatsiya bor, xolos).

Sayt bilan ishlash strategiyasi boshqa bo'limlardagidek, lekin ALOHIDA
hisoblagichlar bilan — bo'limlar bir-birining navbatini band qilmaydi.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from html import unescape

import aiohttp
import redis.asyncio as aioredis

from config import REDIS_DB
from src.db import database
from src.utils.mandat_parser import USER_AGENT, MandatBusy, MandatUnavailable

BASE = "https://mandat.uzbmb.uz/Bakalavr"
SEARCH_URL = f"{BASE}/MainSearch"
PAGINATE_URL = f"{BASE}/Paginate"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25, connect=15)
RETRY_COUNT = 2

# Bu bo'lim uchun alohida chegaralar (natija/yo'nalish bo'limlaridan mustaqil)
semaphore = asyncio.Semaphore(4)
MAX_QUEUE = 15
_waiting = 0
_inflight: dict[str, asyncio.Task] = {}

FETCH_DEADLINE = 20      # foydalanuvchining kutish chegarasi
RANK_FRESH_TTL = 3 * 3600    # o'rin snapshot'ining yangilik muddati
STATS_FRESH_TTL = 12 * 3600  # kombinatsiya agregatlarining yangilik muddati
NEG_TTL = 10 * 60

BULK_PAGE_SIZE = 50      # Paginate'da server ruxsat bergan eng katta qiymat
PROBE_DELAY = 0.15       # ikkilik qidiruvdagi so'rovlar orasidagi pauza
MAX_PROBE_PAGE = 40000   # xavfsizlik chegarasi

NOMINAL_MAX = 189.0      # nominal eng yuqori ball (undan yuqorisi imtiyoz bilan)
PASS_MARK = 56.7         # o'tish chegarasi

LADDER_RANKS = (100, 1000, 5000, 10000, 25000)

NEG_PREFIX = "mandat:orin:neg:"
LANG_NAMES = {1: "O'zbekcha", 2: "Русский", 3: "Qoraqalpoq",
              4: "Tadjik", 5: "Qozoq", 6: "Turkman", 7: "Qirg'iz"}

redis = aioredis.Redis(host="localhost", port=6379, db=REDIS_DB, decode_responses=True)

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()
_stats_tasks: dict[str, asyncio.Task] = {}


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    return _session


async def close_session() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


# ============ HTML tahlili ============

_CARD_RE = re.compile(
    r'm3-rescard__name"><i[^>]*></i>\s*(?P<fio>[^<]*)</div>\s*'
    r'<div class="m3-rescard__id">#\s*(?P<id>\d+)</div>'
    r'(?P<rest>.*?)(?=m3-rescard__name|<nav|\Z)', re.S)
_BALL_RE = re.compile(r'm3-score-val[^>]*>\s*([\d,.]+)\s*<')


def _to_float(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.replace(",", ".").strip())
    except ValueError:
        return None


def parse_cards(html: str) -> list[dict]:
    """Sahifadagi abituriyent kartalari (tartibi saqlanadi)."""
    cards = []
    for m in _CARD_RE.finditer(html):
        ball_m = _BALL_RE.search(m.group("rest"))
        cards.append({
            "fio": unescape(m.group("fio")).strip(),
            "abt_id": m.group("id"),
            "ball": _to_float(ball_m.group(1)) if ball_m else None,
        })
    return cards


def _hidden(html: str, name: str) -> str | None:
    m = re.search(rf'name="{name}" value="([^"]*)"', html)
    return m.group(1) if m else None


def parse_rank_page(html: str, abt_id: str) -> dict | None:
    """ID qidiruvi javobidan o'rin va kombinatsiyani ajratib oladi."""
    cards = parse_cards(html)
    if not cards:
        return None
    idx = next((i for i, c in enumerate(cards, 1) if c["abt_id"] == abt_id), None)
    if idx is None:
        return None

    m = re.search(r'page-item active"[^>]*>.*?name="pageNumber" value="(\d+)"', html, re.S)
    if not m:
        return None
    page = int(m.group(1))
    page_size = int(_hidden(html, "pageSize") or 10)

    me = cards[idx - 1]
    lang_id = int(_hidden(html, "edLangId") or 0)
    return {
        "abt_id": abt_id,
        "fio": me["fio"],
        "ball": me["ball"],
        "orin": (page - 1) * page_size + idx,
        "s4subject": _hidden(html, "s4subject") or "",
        "s5subject": _hidden(html, "s5subject") or "",
        "ed_lang_id": lang_id,
        "ed_lang": LANG_NAMES.get(lang_id, "—"),
    }


# ============ Saytga so'rovlar (himoyalangan) ============

def _release_slot(_task: asyncio.Task, key: str) -> None:
    global _waiting
    _waiting -= 1
    _inflight.pop(key, None)


async def _request(url: str, params: dict) -> str:
    session = await _get_session()
    last_err: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            async with semaphore:
                async with session.get(url, params=params) as resp:
                    return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = e
            logging.warning(f"O'rin so'rovi muvaffaqiyatsiz ({attempt}-urinish): {e}")
            if attempt < RETRY_COUNT:
                await asyncio.sleep(2)
    raise MandatUnavailable(str(last_err))


async def _page_cards(s4: str, s5: str, lang: int, page: int,
                      page_size: int = BULK_PAGE_SIZE) -> list[dict]:
    html = await _request(PAGINATE_URL, {
        "pageNumber": page, "pageSize": page_size,
        "s4subject": s4, "s5subject": s5, "edLangId": lang,
    })
    return parse_cards(html)


# ============ Kombinatsiya agregatlari (ikkilik qidiruv) ============

async def _find_total(s4: str, s5: str, lang: int) -> int:
    """Oxirgi to'la sahifani topib, jami abituriyentlar sonini hisoblaydi."""
    lo, hi = 1, 1
    while True:
        n = len(await _page_cards(s4, s5, lang, hi))
        await asyncio.sleep(PROBE_DELAY)
        if n < BULK_PAGE_SIZE:
            break
        lo, hi = hi, hi * 2
        if hi > MAX_PROBE_PAGE:
            break
    if hi == 1:  # birinchi sahifaning o'zi to'la emas
        return len(await _page_cards(s4, s5, lang, 1))
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        n = len(await _page_cards(s4, s5, lang, mid))
        await asyncio.sleep(PROBE_DELAY)
        if n == BULK_PAGE_SIZE:
            lo = mid
        else:
            hi = mid
    tail = len(await _page_cards(s4, s5, lang, lo + 1))
    return lo * BULK_PAGE_SIZE + tail


async def _count_at_least(s4: str, s5: str, lang: int,
                          threshold: float, total: int) -> int:
    """ball >= threshold bo'lganlar soni (ro'yxat kamayish tartibida)."""
    if total <= 0:
        return 0
    last_page = -(-total // BULK_PAGE_SIZE)
    first = await _page_cards(s4, s5, lang, 1)
    await asyncio.sleep(PROBE_DELAY)
    if not first or (first[0]["ball"] or -1) < threshold:
        return 0

    lo, hi = 1, last_page + 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        cards = await _page_cards(s4, s5, lang, mid)
        await asyncio.sleep(PROBE_DELAY)
        head = cards[0]["ball"] if cards else None
        if head is not None and head >= threshold:
            lo = mid
        else:
            hi = mid
    cards = await _page_cards(s4, s5, lang, lo)
    return (lo - 1) * BULK_PAGE_SIZE + sum(
        1 for c in cards if (c["ball"] or -1) >= threshold)


async def _ball_at_rank(s4: str, s5: str, lang: int, rank: int) -> float | None:
    """Berilgan o'rindagi ball — bitta so'rov."""
    page = -(-rank // BULK_PAGE_SIZE)
    idx = (rank - 1) % BULK_PAGE_SIZE
    cards = await _page_cards(s4, s5, lang, page)
    return cards[idx]["ball"] if idx < len(cards) else None


def combo_key(s4: str, s5: str, lang: int) -> str:
    return f"{s4}|{s5}|{lang}"


async def _compute_stats(s4: str, s5: str, lang: int, total: int | None = None) -> dict:
    """Kombinatsiya bo'yicha to'liq agregatlar (~30 so'rov, bir marta)."""
    if total is None:
        total = await _find_total(s4, s5, lang)

    max_count = await _count_at_least(s4, s5, lang, NOMINAL_MAX, total)
    pass_count = await _count_at_least(s4, s5, lang, PASS_MARK, total)

    ladder = {}
    for r in LADDER_RANKS:
        if r < total:
            b = await _ball_at_rank(s4, s5, lang, r)
            await asyncio.sleep(PROBE_DELAY)
            if b is not None:
                ladder[str(r)] = b

    stats = {
        "jami": total,
        "max_ball_count": max_count,
        "below_pass_count": max(0, total - pass_count),
        "ladder": ladder,
        "full": True,
    }
    await _save_stats(s4, s5, lang, stats)
    return stats


async def _save_stats(s4: str, s5: str, lang: int, stats: dict) -> None:
    try:
        await database.execute(
            """
            INSERT INTO orin_stats (combo_key, s4subject, s5subject, ed_lang_id,
                                    jami, max_ball_count, below_pass_count,
                                    ladder, full_computed, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (combo_key) DO UPDATE SET
                jami = EXCLUDED.jami,
                max_ball_count = EXCLUDED.max_ball_count,
                below_pass_count = EXCLUDED.below_pass_count,
                ladder = EXCLUDED.ladder,
                full_computed = EXCLUDED.full_computed,
                computed_at = NOW()
            """,
            (combo_key(s4, s5, lang), s4, s5, lang, stats["jami"],
             stats.get("max_ball_count"), stats.get("below_pass_count"),
             json.dumps(stats.get("ladder") or {}), stats.get("full", False)),
        )
    except Exception:
        logging.exception(f"Agregatlarni saqlab bo'lmadi ({combo_key(s4, s5, lang)})")


async def _load_stats(s4: str, s5: str, lang: int) -> dict | None:
    try:
        row = await database.fetchone(
            """SELECT jami, max_ball_count, below_pass_count, ladder, full_computed,
                      computed_at >= NOW() - make_interval(secs => %s)
               FROM orin_stats WHERE combo_key = %s""",
            (STATS_FRESH_TTL, combo_key(s4, s5, lang)),
        )
    except Exception:
        logging.exception("Agregatlarni o'qib bo'lmadi")
        return None
    if not row:
        return None
    ladder = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
    return {
        "jami": row[0], "max_ball_count": row[1], "below_pass_count": row[2],
        "ladder": ladder, "full": bool(row[4]), "fresh": bool(row[5]),
    }


def _schedule_full_stats(s4: str, s5: str, lang: int, total: int | None) -> None:
    """To'liq agregatlarni fonda hisoblash (foydalanuvchini kuttirmaymiz)."""
    key = combo_key(s4, s5, lang)
    task = _stats_tasks.get(key)
    if task is not None and not task.done():
        return

    async def _runner():
        try:
            await _compute_stats(s4, s5, lang, total)
            logging.info(f"O'rin agregatlari hisoblandi: {key}")
        except Exception:
            logging.exception(f"Agregatlarni hisoblab bo'lmadi: {key}")

    t = asyncio.create_task(_runner())
    _stats_tasks[key] = t
    t.add_done_callback(lambda _t, _k=key: _stats_tasks.pop(_k, None))


async def get_stats(s4: str, s5: str, lang: int) -> dict:
    """Kombinatsiya agregatlari. 'jami' doim bo'ladi; qolganlari fonda tayyorlanadi."""
    cached = await _load_stats(s4, s5, lang)
    if cached and cached["fresh"]:
        if not cached["full"]:
            _schedule_full_stats(s4, s5, lang, cached["jami"])
        return cached

    # Yangi (yoki eskirgan) — 'jami'ni darhol hisoblaymiz, qolganini fonda
    try:
        total = await _find_total(s4, s5, lang)
    except Exception:
        if cached:
            return {**cached, "fresh": False}
        raise
    stats = {"jami": total, "max_ball_count": None, "below_pass_count": None,
             "ladder": {}, "full": False, "fresh": True}
    await _save_stats(s4, s5, lang, stats)
    _schedule_full_stats(s4, s5, lang, total)
    return stats


# ============ Asosiy kirish nuqtasi ============

async def _fetch_and_store(abt_id: str) -> dict:
    """Saytdan o'rinni olish + saqlash — bitta ajralmas fon vazifa."""
    html = await _request(SEARCH_URL, {"entrantid": abt_id, "lang": "uz"})
    info = parse_rank_page(html, abt_id)
    if info is None:
        text = ("❌ Bunday ID topilmadi. Iltimos, ID raqamini tekshiring.\n\n"
                "<i>Mandat saytidagi uzilishlar sababli ham topilmayotgan bo'lishi "
                "mumkin — birozdan so'ng qayta urinib ko'ring.</i>")
        try:
            await redis.set(NEG_PREFIX + abt_id, text, ex=NEG_TTL)
        except Exception as e:
            logging.warning(f"Redis yozish xatosi: {e}")
        return {"text": text}

    try:
        await database.execute(
            """
            INSERT INTO orinlar (abt_id, fio, ball, orin, s4subject, s5subject,
                                 ed_lang_id, result_json, found_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (abt_id) DO UPDATE SET
                fio = EXCLUDED.fio, ball = EXCLUDED.ball, orin = EXCLUDED.orin,
                s4subject = EXCLUDED.s4subject, s5subject = EXCLUDED.s5subject,
                ed_lang_id = EXCLUDED.ed_lang_id,
                result_json = EXCLUDED.result_json, found_at = NOW()
            """,
            (abt_id, info["fio"], info["ball"], info["orin"], info["s4subject"],
             info["s5subject"], info["ed_lang_id"], json.dumps(info)),
        )
    except Exception:
        logging.exception(f"O'rin snapshotini saqlab bo'lmadi (ID={abt_id})")
    return {"info": info}


async def get_rank(abt_id: str) -> dict:
    """{'info': {...}} yoki {'text': ...} (topilmadi).

    Tartib: Redis(neg) -> Postgres(yangi) -> sayt.
    MandatBusy / MandatUnavailable yuqoriga otiladi (eskirgan nusxa bo'lsa — o'sha).
    """
    global _waiting

    try:
        cached = await redis.get(NEG_PREFIX + abt_id)
        if cached:
            return {"text": cached}
    except Exception as e:
        logging.warning(f"Redis o'qish xatosi: {e}")

    stale = None
    try:
        row = await database.fetchone(
            """SELECT result_json, found_at >= NOW() - make_interval(secs => %s)
               FROM orinlar WHERE abt_id = %s""",
            (RANK_FRESH_TTL, abt_id),
        )
        if row:
            info = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            if row[1]:
                return {"info": info, "stale": False}
            stale = info
    except Exception:
        logging.exception(f"O'rin snapshotini o'qib bo'lmadi (ID={abt_id})")

    task = _inflight.get(abt_id)
    if task is None:
        if _waiting >= MAX_QUEUE:
            if stale is not None:
                return {"info": stale, "stale": True}
            raise MandatBusy()
        _waiting += 1  # tekshiruv bilan bitta sinxron blokda — poyga yo'q
        task = asyncio.create_task(_fetch_and_store(abt_id))
        _inflight[abt_id] = task
        task.add_done_callback(lambda _t, _id=abt_id: _release_slot(_t, _id))

    try:
        res = await asyncio.wait_for(asyncio.shield(task), timeout=FETCH_DEADLINE)
        if "info" in res:
            return {"info": res["info"], "stale": False}
        return res
    except (asyncio.TimeoutError, MandatUnavailable):
        if stale is not None:
            return {"info": stale, "stale": True}
        raise MandatUnavailable(f"javob {FETCH_DEADLINE}s ichida kelmadi")


# ============ Ko'rsatish ============

def _num(n) -> str:
    """1234567 -> '1 234 567'"""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _ball(b) -> str:
    if b is None:
        return "—"
    return f"{b:.1f}".replace(".", ",")


def format_main(info: dict, stats: dict | None, stale: bool = False) -> str:
    """Asosiy xabar: o'rin, ball, foizli holat."""
    lines = [
        "🏅 <b>O'rin aniqlash</b>",
        "━━━━━━━━━━━━━━",
        f"🪪 {info.get('fio') or '—'}",
        f"🆔 <b>{info['abt_id']}</b>",
        f"📚 {info.get('s4subject')} + {info.get('s5subject')}",
        f"🗣 {info.get('ed_lang')}",
        "",
        f"🎓 To'plangan ball: <b>{_ball(info.get('ball'))}</b>",
    ]

    orin = info.get("orin")
    jami = (stats or {}).get("jami")
    if orin and jami:
        lines.append(f"🏆 Reytingdagi o'rningiz: <b>{_num(orin)}</b> / {_num(jami)}")
        pct = orin / jami * 100
        lines.append("")
        lines.append(f"📊 Siz eng yaxshi <b>{pct:.1f}%</b> ichidasiz")
        lines.append(f"🔺 Sizdan yuqorida: {_num(orin - 1)} ta")
        lines.append(f"🔻 Sizdan pastda: {_num(jami - orin)} ta")
    elif orin:
        lines.append(f"🏆 Reytingdagi o'rningiz: <b>{_num(orin)}</b>")

    ball = info.get("ball")
    if ball is not None:
        lines.append("")
        if ball >= PASS_MARK:
            lines.append(f"✅ Ballingiz o'tish chegarasidan ({_ball(PASS_MARK)}) yuqori")
        else:
            lines.append(f"⚠️ Ballingiz o'tish chegarasidan ({_ball(PASS_MARK)}) past")

    lines.append("")
    if stale:
        lines.append("⚠️ <i>Sayt hozir javob bermayapti — oldinroq olingan ma'lumot.</i>")
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>")
    return "\n".join(lines)


def format_details(info: dict, stats: dict | None) -> str:
    """'Batafsil' — kombinatsiya bo'yicha kengaytirilgan statistika."""
    lines = [
        "📊 <b>Batafsil statistika</b>",
        "━━━━━━━━━━━━━━",
        f"📚 {info.get('s4subject')} + {info.get('s5subject')}",
        f"🗣 {info.get('ed_lang')}",
        "",
    ]

    if not stats or not stats.get("jami"):
        lines.append("⏳ Statistika hali hisoblanmoqda. Birozdan so'ng qayta bosing.")
        return "\n".join(lines)

    jami = stats["jami"]
    lines.append(f"👥 Ushbu fan majmuasida jami: <b>{_num(jami)}</b> ta abituriyent")

    if stats.get("full"):
        lines.append("")
        lines.append(f"🥇 {_ball(NOMINAL_MAX)} va undan yuqori ball: "
                     f"<b>{_num(stats.get('max_ball_count'))}</b> ta")
        lines.append(f"⚠️ O'tish chegarasidan ({_ball(PASS_MARK)}) past: "
                     f"<b>{_num(stats.get('below_pass_count'))}</b> ta")

        ladder = stats.get("ladder") or {}
        if ladder:
            lines.append("")
            lines.append("🪜 <b>Ball darajalari</b> (shu o'ringa kirish uchun):")
            for r in LADDER_RANKS:
                b = ladder.get(str(r))
                if b is not None:
                    lines.append(f"    • Top {_num(r)} — <b>{_ball(b)}</b> ball")
    else:
        lines.append("")
        lines.append("⏳ Qo'shimcha statistika hisoblanmoqda — birozdan so'ng qayta bosing.")

    orin, ball = info.get("orin"), info.get("ball")
    if orin:
        lines.append("")
        lines.append(f"🎯 <b>Sizning natijangiz:</b> {_ball(ball)} ball, "
                     f"{_num(orin)}-o'rin")
        ladder = stats.get("ladder") or {}
        # Eng yaqin (erishish oson) maqsad — o'rindan kichik eng katta daraja
        reachable = [r for r in LADDER_RANKS
                     if r < orin and ladder.get(str(r)) is not None]
        if reachable and ball is not None:
            nxt = max(reachable)
            farq = ladder[str(nxt)] - ball
            if farq > 0:
                lines.append(f"📈 Top {_num(nxt)} ga kirish uchun yana "
                             f"<b>{_ball(farq)}</b> ball kerak edi")

    return "\n".join(lines)
