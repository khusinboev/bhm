import logging
import time
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from bs4 import BeautifulSoup
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from datetime import datetime

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

data_router = Router()

# Thread pool cheklash
executor = ThreadPoolExecutor(max_workers=5)  # Maksimal 5 ta thread


@dataclass
class AbiturientInfo:
    fio: str
    user_id: str
    fanlar: list
    umumiy_ball: str
    vaqt: str


class MainState2(StatesGroup):
    natija = State()


@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    try:
        await state.clear()
    except:
        pass


@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    from_chat_id = "@Second_Polat"
    message_id = 733
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_markup=await UserPanels.to_back(),
    )
    await state.set_state(MainState2.natija)


class AbiturientScraper:
    def __init__(self):
        self.session = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    async def get_info_async(self, abt_id: str) -> Optional[AbiturientInfo]:
        """Asinxron tarzda ma'lumot olish"""
        try:
            async with aiohttp.ClientSession(headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)) as session:
                # Birinchi so'rov - asosiy sahifa
                async with session.get("https://mandat.uzbmb.uz/") as response:
                    if response.status != 200:
                        return None

                    main_page = await response.text()
                    soup = BeautifulSoup(main_page, 'html.parser')

                    # CSRF token olish (agar kerak bo'lsa)
                    csrf_token = soup.find('input', {'name': '__token'})
                    csrf_value = csrf_token.get('value') if csrf_token else None

                # Qidiruv so'rovi
                search_data = {
                    'AbiturID': abt_id,
                    '__token': csrf_value
                } if csrf_value else {'AbiturID': abt_id}

                async with session.post("https://mandat.uzbmb.uz/", data=search_data) as response:
                    if response.status != 200:
                        return None

                    search_result = await response.text()
                    soup = BeautifulSoup(search_result, 'html.parser')

                    # Detail linkni topish
                    detail_link = soup.find('a', class_='btn btn-info')
                    if not detail_link:
                        return None

                    detail_url = detail_link.get('href')
                    if not detail_url.startswith('http'):
                        detail_url = f"https://mandat.uzbmb.uz{detail_url}"

                # Detail sahifani olish
                async with session.get(detail_url) as response:
                    if response.status != 200:
                        return None

                    detail_page = await response.text()
                    return self.parse_detail_page(detail_page, abt_id)

        except Exception as e:
            logging.error(f"Asinxron scraping xatosi: {e}")
            return None

    def get_info_sync(self, abt_id: str) -> Optional[AbiturientInfo]:
        """Sinxron tarzda ma'lumot olish (fallback)"""
        try:
            import requests

            session = requests.Session()
            session.headers.update(self.headers)

            # Asosiy sahifa
            response = session.get("https://mandat.uzbmb.uz/", timeout=30)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            csrf_token = soup.find('input', {'name': '__token'})
            csrf_value = csrf_token.get('value') if csrf_token else None

            # Qidiruv
            search_data = {
                'AbiturID': abt_id,
                '__token': csrf_value
            } if csrf_value else {'AbiturID': abt_id}

            response = session.post("https://mandat.uzbmb.uz/", data=search_data, timeout=30)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            detail_link = soup.find('a', class_='btn btn-info')
            if not detail_link:
                return None

            detail_url = detail_link.get('href')
            if not detail_url.startswith('http'):
                detail_url = f"https://mandat.uzbmb.uz{detail_url}"

            # Detail sahifa
            response = session.get(detail_url, timeout=30)
            if response.status_code != 200:
                return None

            return self.parse_detail_page(response.text, abt_id)

        except Exception as e:
            logging.error(f"Sinxron scraping xatosi: {e}")
            return None

    def parse_detail_page(self, html: str, abt_id: str) -> Optional[AbiturientInfo]:
        """HTML sahifasini parse qilish"""
        try:
            soup = BeautifulSoup(html, 'html.parser')

            # FIO olish
            fio_element = soup.find('div', string=lambda text: text and 'F.I.SH' in text)
            if fio_element:
                fio_bold = fio_element.find('b')
                fio = fio_bold.text.strip() if fio_bold else "Noma'lum"
            else:
                fio = "Noma'lum"

            # Ball ma'lumotlarini olish
            card_headers = soup.select("div.card-header.card-div.text-center")
            fanlar = []

            for header in card_headers:
                text = header.get_text(strip=True)
                if "To'g'ri javoblar soni" in text:
                    bolds = header.find_all("b")
                    if len(bolds) >= 2:
                        correct = bolds[0].text.strip()
                        score = bolds[1].text.strip()
                        fanlar.append((correct, score))
                    if len(fanlar) >= 3:
                        break

            # Umumiy ball
            umumiy_ball = "?"
            umumiy_div = soup.find("div", class_="card-header card-div text-center",
                                   string=lambda t: t and "Umumiy ball" in t)
            if not umumiy_div:
                umumiy_div = soup.find("div", class_="bg-success")

            if umumiy_div:
                umumiy_b = umumiy_div.find("b")
                if umumiy_b:
                    umumiy_ball = umumiy_b.text.strip()

            vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            return AbiturientInfo(
                fio=fio,
                user_id=abt_id,
                fanlar=fanlar,
                umumiy_ball=umumiy_ball,
                vaqt=vaqt
            )

        except Exception as e:
            logging.error(f"HTML parse xatosi: {e}")
            return None


# Global scraper instance
scraper = AbiturientScraper()


async def get_abiturient_info(abt_id: str) -> str:
    """Ma'lumotni olish uchun asosiy funksiya"""
    try:
        # Avval asinxron usulni sinab ko'ramiz
        info = await scraper.get_info_async(abt_id)

        # Agar asinxron ishlamasa, sinxron usulni ishlatamiz
        if not info:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(executor, scraper.get_info_sync, abt_id)

        if not info:
            return f"❌ ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi."

        # Natijani formatlash
        fanlar_text = ""
        for i, (correct, score) in enumerate(info.fanlar, 1):
            fan_name = ["Majburiy fanlar", "1-mutaxassislik fani", "2-mutaxassislik fani"][i - 1]
            fanlar_text += f"{i}️⃣ {fan_name}\nTo'g'ri javoblar soni: {correct} ta\nBall: {score}\n\n"

        matn = f"""<b>BAKALAVR 2025</b>
_______
<b>FIO</b>: {info.fio}
🆔: <b>{info.user_id}</b>
_______
{fanlar_text}_______
✅ <b>Umumiy ball:</b> {info.umumiy_ball}
⏰ {info.vaqt}

<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>"""

        return matn

    except Exception as e:
        logging.error(f"Get info xatosi: {e}")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."


@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()

    # Loading message
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    try:
        # Ma'lumotni olish
        result = await get_abiturient_info(abt_id)

        # Loading message ni o'chirish
        await loading_msg.delete()

        # Natijani yuborish
        await msg.answer(result, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Handler xatosi: {e}")
        await loading_msg.delete()
        await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")


@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def handle_id_query2(msg: Message):
    from_chat_id = "@Second_Polat"
    message_id = 733
    await bot.copy_message(
        chat_id=msg.chat.id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_markup=await UserPanels.to_back(),
    )