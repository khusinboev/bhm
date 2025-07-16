import logging

import fitz
import aiohttp
from bs4 import BeautifulSoup
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

data_router = Router()


async def get_abiturient_info_by_id(user_id: int | str) -> str:
    detail_url = f"https://mandat.uzbmb.uz/home/abiturient/detail?id={user_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(detail_url, timeout=10) as response:
                if response.status == 404:
                    return "❌ Ma'lumot topilmadi (404)."
                elif response.status != 200:
                    return f"❌ Server javobi: {response.status}"

                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")

        # H1 - FIO
        h1 = soup.find("h1")
        if not h1:
            return "❌ Ma'lumot topilmadi yoki sahifa strukturasida o'zgarish bor."

        fio = h1.text.strip()

        info_block = soup.find("div", class_="card-body")
        if not info_block:
            return "❌ Ma'lumot topilmadi: 'card-body' bo'limi yo'q."

        rows = info_block.find_all("tr")
        results = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].text.strip()
                val = cells[1].text.strip()
                results.append(f"{key}: {val}")

        umumiy = soup.find("h3").text.strip()
        vaqt = soup.find_all("p")[-1].text.strip()

        # Formatlash
        result = f"""<b>BAKALAVR 2025 |БАКАЛАВР 2025</b>
___________________________________
<b>FIO| ФИО:</b>  {fio}
___________________________________
🆔:  <code>{user_id}</code>
"""

        for line in results:
            result += f"{line}\n"

        result += f"""___________________________________
🔸<b>{umumiy}</b>
___________________________________
⏰ <i>{vaqt}</i>"""

        return result

    except aiohttp.ClientError:
        return "❌ Sayt bilan bog‘lanishda xatolik yuz berdi. Internetni tekshirib ko‘ring."

    except Exception as e:
        return f"❗ Ma'lumotni parse qilishda xatolik:\n{e}"


@data_router.message(F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.text.strip()

    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    try:
        info_text = await get_abiturient_info_by_id(user_id)

        if "Ma'lumot topilmadi" in info_text or info_text.startswith("❌") or info_text.startswith("❗"):
            await msg.answer(f"🚫 <b>ID: {user_id}</b> uchun ma'lumot topilmadi.\nIltimos, ID to‘g‘riligini tekshiring.", parse_mode="HTML")
        else:
            await msg.answer(info_text, parse_mode="HTML")

    except Exception as e:
        logging.exception("❌ Xatolik yuz berdi:")
        await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring.")
