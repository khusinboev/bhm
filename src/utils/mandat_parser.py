"""mandat.uzbmb.uz saytidan abituriyent ma'lumotlarini olish (Bakalavr 2026).

2026 mavsumida sayt to'liq yangilandi:
  - ID bo'yicha qidiruv oddiy GET so'rov: /Bakalavr/MainSearch?entrantid=<ID>&lang=uz
  - Server o'zi natija sahifasiga redirect qiladi: /Bakalavr/Details?hashId=...
  - Natijalar jadvali va paginatsiya olib tashlangan (saytdagi "o'rin" endi mavjud emas)
  - ID endi qat'iy 7 xonali

Shu sababli Playwright/brauzer kerak emas — aiohttp + BeautifulSoup yetarli
(taxminan 20 barobar tezroq va yengilroq).
"""

import asyncio
import logging
import re
from datetime import datetime

import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "https://mandat.uzbmb.uz"
SEARCH_URL = f"{BASE_URL}/Bakalavr/MainSearch"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

# Cho'qqi yukda slotlar tez bo'shashi uchun timeout qisqartirilgan: 2 urinish x 30s
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=15)
RETRY_COUNT = 2

# Saytga bir vaqtda boradigan so'rovlar chegarasi
semaphore = asyncio.Semaphore(8)

# Navbat himoyasi: shuncha noyob ID kutayotgan bo'lsa, yangilari darhol rad etiladi
MAX_QUEUE = 100
_waiting = 0

# Bir xil ID uchun parallel so'rovlar bitta so'rovga birlashtiriladi
_inflight: dict[str, asyncio.Task] = {}

_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()


class MandatUnavailable(Exception):
    """Sayt javob bermadi (timeout yoki ulanish xatosi)."""


class MandatBusy(Exception):
    """Navbat to'la — foydalanuvchi keyinroq urinishi kerak."""


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
    """Bot to'xtaganda sessiyani yopish uchun."""
    if _session is not None and not _session.closed:
        await _session.close()


def _norm(text: str) -> str:
    """Har xil apostrof belgilarini bitta ko'rinishga keltiradi."""
    return text.replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("‘", "'")


async def fetch_details(abt_id: str) -> dict | None:
    """ID bo'yicha abituriyent sahifasini olib, ma'lumotlar lug'atini qaytaradi.

    Bir xil ID bo'yicha parallel chaqiruvlar saytga bitta so'rovga birlashtiriladi.

    None — bunday ID saytda topilmadi.
    MandatUnavailable — sayt javob bermadi.
    MandatBusy — navbat to'la, so'rov qabul qilinmadi.
    """
    task = _inflight.get(abt_id)
    if task is None:
        if _waiting >= MAX_QUEUE:
            raise MandatBusy()
        task = asyncio.create_task(_fetch_details(abt_id))
        _inflight[abt_id] = task
        task.add_done_callback(lambda _t, _id=abt_id: _inflight.pop(_id, None))
    return await task


async def _fetch_details(abt_id: str) -> dict | None:
    global _waiting
    session = await _get_session()
    last_err: Exception | None = None
    _waiting += 1
    try:
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                async with semaphore:
                    async with session.get(
                        SEARCH_URL,
                        params={"entrantid": abt_id, "lang": "uz"},
                        allow_redirects=True,
                    ) as resp:
                        final_url = str(resp.url)
                        html = await resp.text()
                if "/Bakalavr/Details" not in final_url:
                    # Redirect bo'lmadi — bunday ID mavjud emas
                    return None
                # HTML tahlili CPU ishi — event loop'ni bloklamasligi uchun alohida thread'da
                return await asyncio.to_thread(parse_details, html, abt_id)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                logging.warning(f"mandat.uzbmb.uz so'rovi muvaffaqiyatsiz ({attempt}-urinish, ID={abt_id}): {e}")
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(2)
    finally:
        _waiting -= 1
    raise MandatUnavailable(str(last_err))


def parse_details(html: str, abt_id: str) -> dict | None:
    """Details sahifasi HTML'idan ma'lumotlarni ajratib oladi."""
    soup = BeautifulSoup(html, "html.parser")

    name_el = soup.select_one(".m3-det-hero__name")
    if name_el is None:
        return None
    fio = name_el.get_text(strip=True)
    if not fio:
        return None

    # Mavsum nomi sahifa sarlavhasidan: "Bakalavr 2026 | Mandat"
    season = f"BAKALAVR {datetime.now().year}"
    title_el = soup.find("title")
    if title_el:
        m = re.search(r"(Bakalavr\s+\d{4})", title_el.get_text())
        if m:
            season = m.group(1).upper()

    # Ta'lim tili — hero metadagi "Ta'lim tili: <b>...</b>"
    til = None
    for span in soup.select(".m3-det-hero__meta span"):
        if "tili" in _norm(span.get_text()).lower():
            b = span.find("b")
            if b:
                til = b.get_text(strip=True)

    # Fanlar majmuasi (Majburiy fanlar, 1-/2-mutaxassislik fani)
    fan_majmua = []
    for block in soup.select(".m3-det-subj"):
        lbl = block.select_one(".m3-det-subj__lbl")
        val = block.select_one(".m3-det-subj__val")
        if lbl and val:
            fan_majmua.append((lbl.get_text(strip=True), val.get_text(strip=True)))

    page_text_upper = _norm(soup.get_text(" ", strip=True)).upper()

    # Holat xabari (masalan: "TEST SINOVLARIDA ISHTIROK ETMAGAN!")
    status_msg = None
    for header in soup.select("div.card-header, .m3-hero__status, .m3-empty, .m3-stat-empty"):
        text = header.get_text(" ", strip=True)
        if "ISHTIROK ETMAGAN" in _norm(text).upper():
            status_msg = text + "\n<i>(yoki test natijalari hali e’lon qilinmagan)</i>"
            break

    # Ball bloklari — yangi dizayndagi .m3-score kartalari
    scores = []
    umumiy_ball = None
    for block in soup.select(".m3-score"):
        lbl_el = block.select_one(".m3-score-label")
        val_el = block.select_one(".m3-score-val")
        if not val_el:
            continue
        lbl = lbl_el.get_text(strip=True) if lbl_el else ""
        val = val_el.get_text(strip=True)
        if "umumiy" in _norm(lbl).lower():
            umumiy_ball = val
        else:
            scores.append((lbl, val))

    # Yakka ko'rsatilgan umumiy ball (gauge/hero)
    if umumiy_ball is None:
        hero_score = soup.select_one(".m3-hero__score, .m3-rank-score, .m3-score-val")
        if hero_score:
            umumiy_ball = hero_score.get_text(strip=True)

    # Eski (2025) uslubdagi kartalar uchun zaxira yo'llar
    fanlar = []
    for header in soup.select("div.card-header"):
        text = _norm(header.get_text(" ", strip=True))
        if "To'g'ri javoblar soni" in text:
            bolds = header.find_all("b")
            if len(bolds) == 2:
                fanlar.append((bolds[0].get_text(strip=True), bolds[1].get_text(strip=True)))
        elif umumiy_ball is None and "Umumiy ball" in text:
            b = header.find("b")
            if b:
                umumiy_ball = b.get_text(strip=True)

    # Javoblar varaqasi (natijalar chiqqanda)
    answers = []
    for li in soup.select("li.list-group-item"):
        text = li.get_text(strip=True)
        if not re.match(r"^\d+\s*[.)-]?\s*\S*$", text):
            continue
        classes = li.get("class") or []
        ok = any("success" in c for c in classes)
        answers.append(f"{text.lower()}{'✅' if ok else '❌'}")

    return {
        "abt_id": abt_id,
        "fio": fio,
        "til": til,
        "season": season,
        "fan_majmua": fan_majmua,
        "status_msg": status_msg,
        "participated": "ISHTIROK ETMAGAN" not in page_text_upper,
        "umumiy_ball": umumiy_ball,
        "scores": scores,
        "fanlar": fanlar,
        "answers": answers,
    }


def format_full_report(info: dict) -> str:
    """"📊 Natija" bo'limi uchun to'liq Telegram xabarini tayyorlaydi."""
    vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"<b>{info['season']}</b>", "_______"]
    lines.append(f"<b>FIO</b>:  {info['fio']}")
    lines.append(f"🆔:  <b>{info['abt_id']}</b>")
    if info.get("til"):
        lines.append(f"🗣 Ta'lim tili: {info['til']}")

    if info.get("fan_majmua"):
        lines.append("_______")
        for lbl, val in info["fan_majmua"]:
            lines.append(f"📚 {lbl}: <b>{val}</b>")

    lines.append("_______")

    has_result = bool(info.get("umumiy_ball") or info.get("scores") or info.get("fanlar"))

    if info.get("status_msg"):
        lines.append(f"ℹ️ <b>{info['status_msg']}</b>")

    if info.get("fanlar"):
        for i, (correct, ball) in enumerate(info["fanlar"], 1):
            lines.append(f"{i}️⃣ To'g'ri javoblar soni: {correct} ta — Ball: {ball}")

    for lbl, val in info.get("scores", []):
        lines.append(f"📌 {lbl}: <b>{val}</b>")

    if info.get("umumiy_ball"):
        lines.append(f"✅ <b>Umumiy ball:</b> {info['umumiy_ball']}")

    if not has_result and not info.get("status_msg"):
        lines.append("ℹ️ Natijalar hali e'lon qilinmagan.")

    if info.get("answers"):
        rows = []
        for i in range(0, len(info["answers"]), 5):
            rows.append("   ".join(info["answers"][i:i + 5]))
        lines.append("_______")
        lines.append("📝 <b>Javoblar varaqasi bo‘yicha</b>")
        lines.append(f"<blockquote>{chr(10).join(rows)}</blockquote>")

    lines.append("_______")
    lines.append(f"⏰ {vaqt}")
    lines.append("")
    lines.append("<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>")
    return "\n".join(lines)
