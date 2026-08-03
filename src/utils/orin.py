"""'📊 Mandat saytdagi o'rni' — abituriyentning reytingdagi o'rni va statistika.

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
PASS_MARK = 56.7         # umumiy o'tish chegarasi
PASS_MARK_HIGH = 68.0    # ayrim yo'nalishlar uchun yuqori chegara

# "Raqiblaringiz natijalari" — ball bo'yicha to'plaganlar kesimi
COMPETITOR_THRESHOLDS = (189.0, 170.0, 160.0, 150.0, 140.0,
                         130.0, 120.0, 110.0, 100.0, 90.0, 80.0)
PASS_THRESHOLDS = (PASS_MARK_HIGH, PASS_MARK)

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


# ============ Kombinatsiya agregatlari (ikkilik qidiruv) ============

class _Scan:
    """Bitta majmua ro'yxati ustida hisob-kitob — sahifalar keshlanadi.

    O'nlab chegara bo'yicha ikkilik qidiruvlar bir xil ro'yxat ustida
    ishlagani uchun kesh ularning ishini qayta ishlatadi: saytga so'rov
    bir necha barobar kamayadi.
    """

    def __init__(self, s4: str, s5: str, lang: int):
        self.s4, self.s5, self.lang = s4, s5, lang
        self._cache: dict[int, list[dict]] = {}

    async def page(self, p: int) -> list[dict]:
        if p in self._cache:
            return self._cache[p]
        html = await _request(PAGINATE_URL, {
            "pageNumber": p, "pageSize": BULK_PAGE_SIZE,
            "s4subject": self.s4, "s5subject": self.s5, "edLangId": self.lang,
        })
        await asyncio.sleep(PROBE_DELAY)
        cards = parse_cards(html)
        self._cache[p] = cards
        return cards

    async def total(self) -> int:
        """Oxirgi to'la sahifani topib, jami abituriyentlar sonini hisoblaydi."""
        lo = hi = 1
        while len(await self.page(hi)) == BULK_PAGE_SIZE:
            lo, hi = hi, hi * 2
            if hi > MAX_PROBE_PAGE:
                break
        if hi == 1:  # birinchi sahifaning o'zi to'la emas
            return len(await self.page(1))
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if len(await self.page(mid)) == BULK_PAGE_SIZE:
                lo = mid
            else:
                hi = mid
        return lo * BULK_PAGE_SIZE + len(await self.page(lo + 1))

    async def count_where(self, pred, total: int) -> int:
        """pred(ball) True bo'lganlar soni.

        Ro'yxat ball bo'yicha kamayish tartibida, test topshirmaganlar
        (ballsiz) eng oxirida — predikat monoton, ikkilik qidiruv ishlaydi.
        """
        if total <= 0:
            return 0
        first = await self.page(1)
        if not first or not pred(first[0]["ball"]):
            return 0
        last_page = -(-total // BULK_PAGE_SIZE)
        lo, hi = 1, last_page + 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            cards = await self.page(mid)
            if cards and pred(cards[0]["ball"]):
                lo = mid
            else:
                hi = mid
        cards = await self.page(lo)
        return (lo - 1) * BULK_PAGE_SIZE + sum(1 for c in cards if pred(c["ball"]))

    async def count_at_least(self, threshold: float, total: int) -> int:
        return await self.count_where(
            lambda b: b is not None and b >= threshold, total)

    async def ball_at(self, rank: int) -> float | None:
        """Berilgan o'rindagi ball."""
        if rank < 1:
            return None
        cards = await self.page(-(-rank // BULK_PAGE_SIZE))
        idx = (rank - 1) % BULK_PAGE_SIZE
        return cards[idx]["ball"] if idx < len(cards) else None


def combo_key(s4: str, s5: str, lang: int) -> str:
    return f"{s4}|{s5}|{lang}"


async def _compute_stats(s4: str, s5: str, lang: int, total: int | None = None) -> dict:
    """Kombinatsiya bo'yicha to'liq agregatlar (bir marta, fonda)."""
    scan = _Scan(s4, s5, lang)
    if total is None:
        total = await scan.total()

    # Test topshirganlar — ballsizlar (kelmaganlar) ro'yxat oxirida turadi
    topshirgan = await scan.count_where(lambda b: b is not None, total)

    # "Raqiblaringiz natijalari" kesimi + o'tish chegaralari
    thresholds = {}
    for t in (*COMPETITOR_THRESHOLDS, *PASS_THRESHOLDS):
        thresholds[f"{t:g}"] = await scan.count_at_least(t, total)

    ladder = {}
    for r in LADDER_RANKS:
        if r < total:
            b = await scan.ball_at(r)
            if b is not None:
                ladder[str(r)] = b

    pass_count = thresholds.get(f"{PASS_MARK:g}", 0)
    stats = {
        "jami": total,
        "topshirgan": topshirgan,
        "thresholds": thresholds,
        "max_ball_count": thresholds.get(f"{NOMINAL_MAX:g}"),
        # Chegaradan past — faqat test topshirganlar orasida
        "below_pass_count": max(0, topshirgan - pass_count),
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
                                    jami, topshirgan, max_ball_count,
                                    below_pass_count, ladder, thresholds,
                                    full_computed, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (combo_key) DO UPDATE SET
                jami = EXCLUDED.jami,
                topshirgan = EXCLUDED.topshirgan,
                max_ball_count = EXCLUDED.max_ball_count,
                below_pass_count = EXCLUDED.below_pass_count,
                ladder = EXCLUDED.ladder,
                thresholds = EXCLUDED.thresholds,
                full_computed = EXCLUDED.full_computed,
                computed_at = NOW()
            """,
            (combo_key(s4, s5, lang), s4, s5, lang, stats["jami"],
             stats.get("topshirgan"), stats.get("max_ball_count"),
             stats.get("below_pass_count"),
             json.dumps(stats.get("ladder") or {}),
             json.dumps(stats.get("thresholds") or {}), stats.get("full", False)),
        )
    except Exception:
        logging.exception(f"Agregatlarni saqlab bo'lmadi ({combo_key(s4, s5, lang)})")


async def _load_stats(s4: str, s5: str, lang: int) -> dict | None:
    try:
        row = await database.fetchone(
            """SELECT jami, topshirgan, max_ball_count, below_pass_count, ladder,
                      thresholds, full_computed,
                      computed_at >= NOW() - make_interval(secs => %s)
               FROM orin_stats WHERE combo_key = %s""",
            (STATS_FRESH_TTL, combo_key(s4, s5, lang)),
        )
    except Exception:
        logging.exception("Agregatlarni o'qib bo'lmadi")
        return None
    if not row:
        return None
    ladder = row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}")
    thresholds = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
    return {
        "jami": row[0], "topshirgan": row[1], "max_ball_count": row[2],
        "below_pass_count": row[3], "ladder": ladder, "thresholds": thresholds,
        "full": bool(row[6]), "fresh": bool(row[7]),
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
        total = await _Scan(s4, s5, lang).total()
    except Exception:
        if cached:
            return {**cached, "fresh": False}
        raise
    stats = {"jami": total, "topshirgan": None, "max_ball_count": None,
             "below_pass_count": None, "ladder": {}, "thresholds": {},
             "full": False, "fresh": True}
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
        "📊 <b>Mandat saytdagi o'rningiz</b>",
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
    """'Batafsil' — raqiblar kesimi: ball bo'yicha to'plaganlar taqsimoti."""
    lines = [
        "📊 <b>Raqiblaringiz natijalari haqida</b>",
        "━━━━━━━━━━━━━━",
        f"📚 {info.get('s4subject')} + {info.get('s5subject')}",
        f"🗣 {info.get('ed_lang')}",
    ]

    if not stats or not stats.get("jami"):
        lines.append("")
        lines.append("⏳ Statistika hali hisoblanmoqda. Birozdan so'ng qayta bosing.")
        return "\n".join(lines)

    jami = stats["jami"]
    topshirgan = stats.get("topshirgan")
    if topshirgan:
        lines.append(f"👥 Jami: <b>{_num(jami)}</b> | "
                     f"✍️ Test topshirgan: <b>{_num(topshirgan)}</b>")
    else:
        lines.append(f"👥 Ushbu fan majmuasida jami: <b>{_num(jami)}</b> ta")

    if not stats.get("full"):
        lines.append("")
        lines.append("⏳ Batafsil statistika hisoblanmoqda — "
                     "birozdan so'ng qayta bosing.")
        return "\n".join(lines)

    thresholds = stats.get("thresholds") or {}
    ball = info.get("ball")

    if thresholds:
        lines.append("")
        # Har bir daraja alohida quote blokida — Telegramda ajratib ko'rinadi
        first = True
        for t in COMPETITOR_THRESHOLDS:
            cnt = thresholds.get(f"{t:g}")
            if cnt is None:
                continue
            icon = "📈 " if first else ""
            first = False
            # Foydalanuvchining o'z darajasi ajratib ko'rsatiladi:
            # ball shu chegaradan yuqori, lekin keyingisiga yetmagan
            mark = " ⬅️ siz" if (ball is not None and t <= ball < _next_threshold(t)) else ""
            lines.append(f"<blockquote>{icon}<b>{t:g}+</b> ball to'plaganlar: "
                         f"{_num(cnt)} ta{mark}</blockquote>")

        lines.append("")
        lines.append("🎯 <b>Minimal o'tish ballari bo'yicha</b>")
        for t, icon in ((PASS_MARK_HIGH, "✅"), (PASS_MARK, "☑️")):
            cnt = thresholds.get(f"{t:g}")
            if cnt is not None:
                lines.append(f"{icon} <b>{_ball(t)}+</b> ball to'plaganlar: "
                             f"{_num(cnt)} ta")
        lines.append(f"❌ Natijasi {_ball(PASS_MARK)} dan pastlar: "
                     f"{_num(stats.get('below_pass_count'))} ta")

    ladder = stats.get("ladder") or {}
    orin = info.get("orin")
    if orin:
        lines.append("")
        lines.append(f"🏆 <b>Sizning natijangiz:</b> {_ball(ball)} ball, "
                     f"{_num(orin)}-o'rin")
        # Eng yaqin (erishish oson) maqsad — o'rindan kichik eng katta daraja
        reachable = [r for r in LADDER_RANKS
                     if r < orin and ladder.get(str(r)) is not None]
        if reachable and ball is not None:
            nxt = max(reachable)
            farq = ladder[str(nxt)] - ball
            if farq > 0:
                lines.append(f"📈 Top {_num(nxt)} ga kirish uchun yana "
                             f"<b>{_ball(farq)}</b> ball kerak edi")

    lines.append("")
    lines.append("<i>O'rin rasmiy UZBMB kengaytirilgan qidiruvidagi joriy "
                 "tartib bo'yicha hisoblandi.</i>")
    return "\n".join(lines)


def _next_threshold(t: float) -> float:
    """Berilgan chegaradan keyingi (yuqoriroq) chegara."""
    idx = COMPETITOR_THRESHOLDS.index(t)
    return COMPETITOR_THRESHOLDS[idx - 1] if idx > 0 else float("inf")
