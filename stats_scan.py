"""Umumiy statistika skaneri — botdan MUTLAQO MUSTAQIL.

Kengaytirilgan qidiruv ro'yxatlari (fan majmuasi + ta'lim tili kesimida)
ball bo'yicha kamayish tartibida saralangan. Shundan foydalanib har bir
majmua uchun aniq sonlarni IKKILIK QIDIRUV bilan topamiz — butun bazani
ko'chirmasdan:

    jami / test topshirgan / kelmagan / ball oraliqlari / persentillar

Botga hech qanday aloqasi yo'q: o'z HTTP sessiyasi, o'z sekin sur'ati,
o'z jadvali (stat_scan). Bot ishlab tursa ham bemalol ishlatiladi.

Ishga tushirish:
    <venv>/bin/python stats_scan.py            # skanerlash (davom ettiradi)
    <venv>/bin/python stats_scan.py --refresh  # hammasini qaytadan
    <venv>/bin/python stats_scan.py --report   # batafsil yig'ma hisobot
    <venv>/bin/python stats_scan.py --post     # kanalga tayyor post (HTML)
"""

import asyncio
import logging
import os
import re
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.db import database  # noqa: E402

BASE = "https://mandat.uzbmb.uz/Bakalavr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

PAGE_SIZE = 50
DELAY = 0.15          # so'rovlar orasidagi pauza — saytga muloyim sur'at
TIMEOUT = aiohttp.ClientTimeout(total=45, connect=15)
MAX_PAGE = 60000

PASS_MARK = 56.7      # o'tish chegarasi
NOMINAL_MAX = 189.0   # nominal eng yuqori ball
# Global gistogramma uchun chegaralar (kamayish tartibida)
THRESHOLDS = [NOMINAL_MAX, 170.0, 150.0, 130.0, 110.0, 90.0, 70.0, PASS_MARK]
PERCENTILES = [("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
               ("p75", 0.75), ("p90", 0.90)]

LANG_NAMES = {1: "O'zbekcha", 2: "Ruscha", 3: "Qoraqalpoqcha",
              4: "Tojikcha", 5: "Qozoqcha", 6: "Turkmancha", 7: "Qirg'izcha"}

_CARD = re.compile(r'm3-rescard__id">#\s*(\d+)</div>(?P<rest>.*?)'
                   r'(?=m3-rescard__name|<nav|\Z)', re.S)
_BALL = re.compile(r'm3-score-val[^>]*>\s*([\d,.]+)\s*<')

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")


async def init_table() -> None:
    await database.execute("""
        CREATE TABLE IF NOT EXISTS public.stat_scan (
            combo_key TEXT PRIMARY KEY,
            s4subject TEXT, s5subject TEXT, ed_lang_id INT, ed_lang TEXT,
            jami INT, topshirgan INT, kelmagan INT,
            buckets JSONB, percentiles JSONB,
            top_ball NUMERIC, requests INT,
            scanned_at TIMESTAMP DEFAULT NOW()
        )""")


class ComboScanner:
    """Bitta (fan majmuasi + til) ro'yxatini skanerlaydi.

    Sahifalar keshlanadi — turli chegaralar bo'yicha ikkilik qidiruvlar
    bir-birining ishini qayta ishlatadi, saytga so'rov keskin kamayadi.
    """

    def __init__(self, session: aiohttp.ClientSession, s4: str, s5: str, lang: int):
        self.s = session
        self.s4, self.s5, self.lang = s4, s5, lang
        self._cache: dict[int, list[float | None]] = {}
        self.requests = 0

    async def page(self, p: int) -> list[float | None]:
        """Sahifadagi ballar (test topshirmaganlar uchun None)."""
        if p in self._cache:
            return self._cache[p]
        params = {"pageNumber": p, "pageSize": PAGE_SIZE, "s4subject": self.s4,
                  "s5subject": self.s5, "edLangId": self.lang}
        for attempt in (1, 2, 3):
            try:
                async with self.s.get(f"{BASE}/Paginate", params=params) as r:
                    html = await r.text()
                break
            except Exception as e:
                if attempt == 3:
                    raise
                logging.warning(f"  so'rov xatosi ({attempt}), qayta urinamiz: {e}")
                await asyncio.sleep(3 * attempt)
        self.requests += 1
        await asyncio.sleep(DELAY)

        out: list[float | None] = []
        for m in _CARD.finditer(html):
            b = _BALL.search(m.group("rest"))
            out.append(float(b.group(1).replace(",", ".")) if b else None)
        self._cache[p] = out
        return out

    async def total(self) -> int:
        """Ro'yxatdagi jami yozuvlar (kelmaganlar bilan birga)."""
        lo = hi = 1
        while len(await self.page(hi)) == PAGE_SIZE:
            lo, hi = hi, hi * 2
            if hi > MAX_PAGE:
                break
        if hi == 1:
            return len(await self.page(1))
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if len(await self.page(mid)) == PAGE_SIZE:
                lo = mid
            else:
                hi = mid
        return lo * PAGE_SIZE + len(await self.page(lo + 1))

    async def count_where(self, pred, total: int) -> int:
        """pred(ball) True bo'lganlar soni.

        Ro'yxat kamayish tartibida bo'lgani uchun predikat monoton:
        boshida True'lar, keyin False'lar (ballsizlar eng oxirida).
        """
        if total <= 0:
            return 0
        first = await self.page(1)
        if not first or not pred(first[0]):
            return 0
        last_page = -(-total // PAGE_SIZE)
        lo, hi = 1, last_page + 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            cards = await self.page(mid)
            if cards and pred(cards[0]):
                lo = mid
            else:
                hi = mid
        cards = await self.page(lo)
        return (lo - 1) * PAGE_SIZE + sum(1 for b in cards if pred(b))

    async def ball_at(self, rank: int) -> float | None:
        if rank < 1:
            return None
        cards = await self.page(-(-rank // PAGE_SIZE))
        idx = (rank - 1) % PAGE_SIZE
        return cards[idx] if idx < len(cards) else None

    async def scan(self) -> dict:
        jami = await self.total()
        if jami == 0:
            return {"jami": 0, "topshirgan": 0, "kelmagan": 0,
                    "buckets": {}, "percentiles": {}, "top_ball": None,
                    "requests": self.requests}

        # Test topshirganlar = balli bor bo'lganlar (ballsizlar ro'yxat oxirida)
        topshirgan = await self.count_where(lambda b: b is not None, jami)

        buckets = {}
        for th in THRESHOLDS:
            buckets[f"{th:g}"] = await self.count_where(
                lambda b, t=th: b is not None and b >= t, jami)

        # Persentillar — faqat test topshirganlar orasida
        percentiles = {}
        for name, q in PERCENTILES:
            rank = max(1, min(topshirgan, int(round(topshirgan * q))))
            if topshirgan > 0:
                val = await self.ball_at(rank)
                if val is not None:
                    percentiles[name] = val

        first = await self.page(1)
        top_ball = first[0] if first and first[0] is not None else None

        return {"jami": jami, "topshirgan": topshirgan,
                "kelmagan": jami - topshirgan, "buckets": buckets,
                "percentiles": percentiles, "top_ball": top_ball,
                "requests": self.requests}


async def fetch_combos(session: aiohttp.ClientSession) -> list[tuple]:
    async def jget(path, **params):
        async with session.get(f"{BASE}/{path}", params=params) as r:
            return await r.json(content_type=None)

    combos = []
    for s4 in await jget("GetS4Subjects"):
        await asyncio.sleep(DELAY)
        for s5 in await jget("GetS5Subjects", s4subject=s4):
            await asyncio.sleep(DELAY)
            for lg in await jget("GetEducLangs", s4subject=s4, s5subject=s5):
                combos.append((s4, s5, lg["edLangId"],
                               LANG_NAMES.get(lg["edLangId"], lg["educlanguage"])))
            await asyncio.sleep(DELAY)
    return combos


async def run_scan(refresh: bool) -> None:
    import json

    await init_table()
    async with aiohttp.ClientSession(headers={"User-Agent": UA},
                                     timeout=TIMEOUT) as session:
        combos = await fetch_combos(session)
        logging.info(f"Jami {len(combos)} ta majmua topildi")

        done = set()
        if not refresh:
            rows = await database.fetchall("SELECT combo_key FROM stat_scan")
            done = {r[0] for r in rows}
            if done:
                logging.info(f"{len(done)} tasi allaqachon skanerlangan — o'tkazib yuboriladi")

        t_start = time.monotonic()
        total_req = 0
        for i, (s4, s5, lang, langname) in enumerate(combos, 1):
            key = f"{s4}|{s5}|{lang}"
            if key in done:
                continue
            try:
                sc = ComboScanner(session, s4, s5, lang)
                res = await sc.scan()
            except Exception:
                logging.exception(f"[{i}/{len(combos)}] XATO: {key}")
                continue

            total_req += res["requests"]
            await database.execute("""
                INSERT INTO stat_scan (combo_key, s4subject, s5subject, ed_lang_id,
                                       ed_lang, jami, topshirgan, kelmagan,
                                       buckets, percentiles, top_ball, requests, scanned_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (combo_key) DO UPDATE SET
                    jami=EXCLUDED.jami, topshirgan=EXCLUDED.topshirgan,
                    kelmagan=EXCLUDED.kelmagan, buckets=EXCLUDED.buckets,
                    percentiles=EXCLUDED.percentiles, top_ball=EXCLUDED.top_ball,
                    requests=EXCLUDED.requests, scanned_at=NOW()
            """, (key, s4, s5, lang, langname, res["jami"], res["topshirgan"],
                  res["kelmagan"], json.dumps(res["buckets"]),
                  json.dumps(res["percentiles"]), res["top_ball"], res["requests"]))

            el = time.monotonic() - t_start
            logging.info(
                f"[{i}/{len(combos)}] {s4} + {s5} ({langname}): "
                f"jami {res['jami']:,}, topshirgan {res['topshirgan']:,}, "
                f"top ball {res['top_ball']} — {res['requests']} so'rov, "
                f"{el/60:.1f} daqiqa o'tdi")

        logging.info(f"TUGADI: {total_req:,} so'rov, "
                     f"{(time.monotonic()-t_start)/60:.1f} daqiqa")


def _n(x) -> str:
    try:
        return f"{int(x):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _pct(part, whole) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


async def _load_rows():
    import json

    rows = await database.fetchall("""
        SELECT s4subject, s5subject, ed_lang, jami, topshirgan, kelmagan,
               buckets, percentiles, top_ball
        FROM stat_scan ORDER BY jami DESC""")
    out = []
    for r in rows:
        b = r[6] if isinstance(r[6], dict) else json.loads(r[6] or "{}")
        p = r[7] if isinstance(r[7], dict) else json.loads(r[7] or "{}")
        out.append({"s4": r[0], "s5": r[1], "lang": r[2], "jami": r[3],
                    "topshirgan": r[4], "kelmagan": r[5], "buckets": b,
                    "pct": p, "top_ball": float(r[8]) if r[8] is not None else None,
                    "otgan": b.get(f"{PASS_MARK:g}", 0)})
    return out


def _totals(rows):
    jami = sum(r["jami"] for r in rows)
    topshirgan = sum(r["topshirgan"] for r in rows)
    agg = {f"{t:g}": sum(r["buckets"].get(f"{t:g}", 0) for r in rows) for t in THRESHOLDS}
    return jami, topshirgan, agg


def _bands(agg, topshirgan):
    """[(yorliq, soni), ...] — ball oraliqlari, yuqoridan pastga."""
    out, prev_t, prev_c = [], None, 0
    for t in THRESHOLDS:
        c = agg[f"{t:g}"]
        if prev_t is None:
            out.append((f"{t:g}+ ball", c))
        else:
            out.append((f"{t:g}–{prev_t:g}", c - prev_c))
        prev_t, prev_c = t, c
    out.append((f"{PASS_MARK:g} dan past", topshirgan - prev_c))
    return out


def _median(agg, topshirgan) -> float:
    """Global median — oraliqlar ichida chiziqli interpolyatsiya (taxminiy)."""
    half = topshirgan / 2
    prev_t, prev_c = None, 0
    for t in THRESHOLDS:
        c = agg[f"{t:g}"]
        if c >= half:
            if prev_t is None:
                return float(t)
            span = c - prev_c
            frac = (half - prev_c) / span if span else 0
            return prev_t - frac * (prev_t - t)
        prev_t, prev_c = t, c
    return PASS_MARK


async def report() -> None:
    rows = await _load_rows()
    if not rows:
        print("Hali skanerlanmagan.")
        return
    jami, topshirgan, agg = _totals(rows)
    kelmagan = jami - topshirgan
    otgan = agg[f"{PASS_MARK:g}"]

    print("=" * 62)
    print("UMUMIY STATISTIKA (Bakalavr 2026)")
    print("=" * 62)
    print(f"{'Majmualar (fan+til)':<26}: {len(rows)}")
    print(f"{'Roʻyxatdagi abituriyent':<26}: {_n(jami)}")
    print(f"{'Test topshirgan':<26}: {_n(topshirgan)} ({_pct(topshirgan, jami)})")
    print(f"{'Testga kelmagan':<26}: {_n(kelmagan)} ({_pct(kelmagan, jami)})")
    print()
    print(f"{'Oʻtish balidan yuqori':<26}: {_n(otgan)} ({_pct(otgan, topshirgan)})")
    print(f"{'Oʻtish balidan past':<26}: {_n(topshirgan - otgan)} "
          f"({_pct(topshirgan - otgan, topshirgan)})")
    print(f"{'Median ball (taxminiy)':<26}: {_median(agg, topshirgan):.1f}")

    print("\nBALL TAQSIMOTI (test topshirganlar orasida)")
    for label, cnt in _bands(agg, topshirgan):
        print(f"  {label:<20}: {_n(cnt):>10}  ({_pct(cnt, topshirgan)})")

    print("\nTAʼLIM TILI BOʻYICHA")
    langs: dict[str, list] = {}
    for r in rows:
        a = langs.setdefault(r["lang"], [0, 0, 0])
        a[0] += r["jami"]; a[1] += r["topshirgan"]; a[2] += r["otgan"]
    for lang, (j, t, o) in sorted(langs.items(), key=lambda x: -x[1][0]):
        print(f"  {lang:<14}: jami {_n(j):>9}, topshirgan {_n(t):>9}, "
              f"oʻtgan {_n(o):>8} ({_pct(o, t)})")

    print("\n1-MUTAXASSISLIK FANI BOʻYICHA (eng ommabop 10 ta)")
    subj: dict[str, list] = {}
    for r in rows:
        a = subj.setdefault(r["s4"], [0, 0, 0])
        a[0] += r["jami"]; a[1] += r["topshirgan"]; a[2] += r["otgan"]
    for s, (j, t, o) in sorted(subj.items(), key=lambda x: -x[1][0])[:10]:
        print(f"  {s:<26}: {_n(j):>9} ta, oʻtgan {_pct(o, t)}")

    print("\nENG KOʻP TANLANGAN 10 MAJMUA")
    for r in rows[:10]:
        print(f"  {r['s4']} + {r['s5']} ({r['lang']}): {_n(r['jami'])} ta, "
              f"oʻtgan {_pct(r['otgan'], r['topshirgan'])}, "
              f"median {r['pct'].get('p50', '—')}")

    big = [r for r in rows if r["topshirgan"] >= 1000]
    print("\nENG RAQOBATLI MAJMUALAR (≥1000 topshirgan, oʻtish foizi past)")
    for r in sorted(big, key=lambda r: r["otgan"] / r["topshirgan"])[:5]:
        print(f"  {r['s4']} + {r['s5']} ({r['lang']}): "
              f"{_pct(r['otgan'], r['topshirgan'])} "
              f"({_n(r['otgan'])}/{_n(r['topshirgan'])})")

    print("\nENG YUQORI OʻTISH FOIZI (≥1000 topshirgan)")
    for r in sorted(big, key=lambda r: -r["otgan"] / r["topshirgan"])[:5]:
        print(f"  {r['s4']} + {r['s5']} ({r['lang']}): "
              f"{_pct(r['otgan'], r['topshirgan'])} "
              f"({_n(r['otgan'])}/{_n(r['topshirgan'])})")

    top = max(rows, key=lambda r: (r["top_ball"] or 0))
    print(f"\nENG YUQORI BALL: {top['top_ball']} — "
          f"{top['s4']} + {top['s5']} ({top['lang']})")
    print(f"189 va undan yuqori ball toʻplaganlar: {_n(agg['189'])}")
    print("=" * 62)


async def make_post() -> None:
    """Kanalga tayyor Telegram post (HTML)."""
    rows = await _load_rows()
    if not rows:
        print("Hali skanerlanmagan.")
        return
    jami, topshirgan, agg = _totals(rows)
    otgan = agg[f"{PASS_MARK:g}"]
    top = max(rows, key=lambda r: (r["top_ball"] or 0))
    big = [r for r in rows if r["topshirgan"] >= 1000]

    langs: dict[str, list] = {}
    for r in rows:
        a = langs.setdefault(r["lang"], [0, 0])
        a[0] += r["topshirgan"]; a[1] += r["otgan"]

    def name(r) -> str:
        """Majmua nomi — o'zbekchadan boshqa tilda bo'lsa til ham ko'rsatiladi."""
        base = f"{r['s4']} + {r['s5']}"
        return base if r["lang"] == "O'zbekcha" else f"{base} ({r['lang']})"

    L = []
    L.append("📊 <b>BAKALAVR-2026 | YAKUNIY STATISTIKA</b>")
    L.append("<i>Respublika bo'yicha to'liq tahlil</i>")
    L.append("")
    L.append("👥 <b>UMUMIY KO'RSATKICHLAR</b>")
    L.append(f"├ Ro'yxatdan o'tgan: <b>{_n(jami)}</b>")
    L.append(f"├ Test topshirgan: <b>{_n(topshirgan)}</b> ({_pct(topshirgan, jami)})")
    L.append(f"└ Testga kelmagan: <b>{_n(jami - topshirgan)}</b> "
             f"({_pct(jami - topshirgan, jami)})")
    L.append("")
    L.append(f"🎯 <b>O'TISH BALI ({PASS_MARK:g})</b>")
    L.append(f"├ ✅ O'tgan: <b>{_n(otgan)}</b> ({_pct(otgan, topshirgan)})")
    L.append(f"└ ❌ O'tmagan: <b>{_n(topshirgan - otgan)}</b> "
             f"({_pct(topshirgan - otgan, topshirgan)})")
    L.append("")
    L.append("📈 <b>BALL TAQSIMOTI</b>")
    bands = _bands(agg, topshirgan)
    for i, (label, cnt) in enumerate(bands):
        pref = "└" if i == len(bands) - 1 else "├"
        L.append(f"{pref} {label}: <b>{_n(cnt)}</b> ({_pct(cnt, topshirgan)})")
    L.append("")
    L.append("🗣 <b>TA'LIM TILI BO'YICHA</b>")
    items = sorted(langs.items(), key=lambda x: -x[1][0])[:4]
    for i, (lang, (t, o)) in enumerate(items):
        pref = "└" if i == len(items) - 1 else "├"
        L.append(f"{pref} {lang}: <b>{_n(t)}</b> ta, o'tgan {_pct(o, t)}")
    L.append("")
    L.append("🔥 <b>ENG KO'P TANLANGAN MAJMUALAR</b>")
    for i, r in enumerate(rows[:5]):
        pref = "└" if i == 4 else "├"
        L.append(f"{pref} {name(r)}: <b>{_n(r['jami'])}</b> ta "
                 f"(o'tgan {_pct(r['otgan'], r['topshirgan'])})")
    L.append("")
    L.append("⚔️ <b>ENG RAQOBATLI MAJMUALAR</b>")
    hard = sorted(big, key=lambda r: r["otgan"] / r["topshirgan"])[:3]
    for i, r in enumerate(hard):
        pref = "└" if i == len(hard) - 1 else "├"
        L.append(f"{pref} {name(r)}: o'tgan atigi "
                 f"<b>{_pct(r['otgan'], r['topshirgan'])}</b>")
    L.append("")
    L.append("🏆 <b>REKORDLAR</b>")
    L.append(f"├ Eng yuqori ball: <b>{top['top_ball']}</b> ({name(top)})")
    L.append(f"├ {NOMINAL_MAX:g} va undan yuqori: <b>{_n(agg['189'])}</b> ta")
    L.append(f"└ Median ball: <b>{_median(agg, topshirgan):.0f}</b> atrofida")
    L.append("")
    L.append("🏅 <i>O'z o'rningizni bilish uchun botdagi "
             "\"O'rin aniqlash\" bo'limidan foydalaning</i>")
    L.append("")
    L.append("👉 @mandat_uzbmbbot")

    text = "\n".join(L)
    print(text)
    print(f"\n[uzunligi: {len(text)} belgi]", file=sys.stderr)


async def main() -> None:
    args = sys.argv[1:]
    try:
        if "--post" in args:
            await make_post()
        elif "--report" in args:
            await report()
        else:
            await run_scan(refresh="--refresh" in args)
    finally:
        await database.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
