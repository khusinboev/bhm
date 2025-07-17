import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, List
import aiohttp
from contextlib import asynccontextmanager

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from bs4 import BeautifulSoup
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import os

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

data_router = Router()


class MainState2(StatesGroup):
    natija = State()


# Driver poolni yaratish
class DriverPool:
    def __init__(self, max_drivers: int = 3):
        self.max_drivers = max_drivers
        self.drivers = []
        self.lock = asyncio.Lock()

    async def get_driver(self):
        async with self.lock:
            if self.drivers:
                return self.drivers.pop()
            else:
                return self._create_driver()

    async def return_driver(self, driver):
        async with self.lock:
            if len(self.drivers) < self.max_drivers:
                self.drivers.append(driver)
            else:
                try:
                    driver.quit()
                except:
                    pass

    def _create_driver(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,720")  # Kichikroq o'lcham
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-web-security")
        options.add_argument("--disable-features=TranslateUI")
        options.add_argument("--disable-ipc-flooding-protection")
        options.add_argument("--memory-pressure-off")
        options.add_argument("--max_old_space_size=4096")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )

        # Performance optimizations
        prefs = {
            "profile.default_content_settings.popups": 0,
            "profile.managed_default_content_settings.images": 2,  # Rasmlarni o'chirish
            "profile.default_content_setting_values.notifications": 2,
            "profile.managed_default_content_settings.stylesheets": 2,  # CSS o'chirish
            "profile.managed_default_content_settings.javascript": 1,  # JS qoldirish
            "profile.managed_default_content_settings.plugins": 1,
            "profile.managed_default_content_settings.media_stream": 2,
        }
        options.add_experimental_option("prefs", prefs)

        return webdriver.Chrome(options=options)


# Global driver pool
driver_pool = DriverPool()

# Executor miqdorini kamaytirish
executor = ThreadPoolExecutor(max_workers=2)

# Cache uchun
cache = {}
CACHE_TIMEOUT = 300  # 5 daqiqa


def get_from_cache(abt_id: str) -> Optional[str]:
    """Keshdan ma'lumot olish"""
    if abt_id in cache:
        data, timestamp = cache[abt_id]
        if time.time() - timestamp < CACHE_TIMEOUT:
            return data
        else:
            del cache[abt_id]
    return None


def save_to_cache(abt_id: str, data: str):
    """Keshga saqlash"""
    cache[abt_id] = (data, time.time())


def get_abiturient_info_by_id(user_id: str) -> str:
    """Ma'lumotlarni olish funksiyasi"""

    # Avval keshdan tekshirish
    cached_data = get_from_cache(user_id)
    if cached_data:
        return cached_data

    driver = driver_pool._create_driver()
    try:
        print(f"🌐 Saytga kirilmoqda... ID: {user_id}")

        # Sahifa yuklanish vaqtini kamaytirish
        driver.set_page_load_timeout(20)
        driver.implicitly_wait(10)

        driver.get("https://mandat.uzbmb.uz/")

        # Sahifa to'liq yuklanishini kutmaslik
        wait = WebDriverWait(driver, 15)

        # Input maydonini topish va ma'lumot kiritish
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))

        # Qidiruv tugmasini bosish
        search_btn = wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))
        driver.execute_script("arguments[0].click();", search_btn)
        print("🔍 Qidiruv bosildi")

        # Detail tugmasini kutish va bosish
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        driver.execute_script("arguments[0].click();", detail_btn)
        print("📄 Batafsil sahifaga o'tildi")

        # Ma'lumotlarni olish
        # FIO olish
        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        # HTML olish va parsing
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Ball ma'lumotlarini olish
        card_headers = soup.select("div.card-header.card-div.text-center")
        fanlar = []
        for header in card_headers:
            text = header.get_text(strip=True)
            if "To’g’ri javoblar soni" in text or "To'g'ri javoblar soni" in text:
                bolds = header.find_all("b")
                if len(bolds) == 2:
                    correct = bolds[0].text.strip()
                    score = bolds[1].text.strip()
                    fanlar.append((correct, score))
                if len(fanlar) >= 3:
                    break  # faqat 3 ta blok yetarli

        # Umumiy ball olish
        umumiy_ball = "?"
        umumiy_div = soup.find("div", class_="card-header card-div text-center",
                               string=lambda t: t and "Umumiy ball" in t)
        if not umumiy_div:
            # Yoki boshqa usul bilan izlash:
            umumiy_div = soup.find("div", class_="bg-success")
        if umumiy_div:
            umumiy_b = umumiy_div.find("b")
            if umumiy_b:
                umumiy_ball = umumiy_b.text.strip()

        # Vaqt
        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        matn = f"""<b>BAKALAVR 2025</b>
_______
<b>FIO</b>:  {fio}
🆔:  <b>{user_id}</b>
_______
1️⃣ Majburiy fanlar 
To'g'ri javoblar soni: {fanlar[0][0]} ta  
Ball: {fanlar[0][1]}

2️⃣ 1-mutaxassislik fani 
To'g'ri javoblar soni: {fanlar[1][0]} ta  
Ball: {fanlar[1][1]}

3️⃣ 2-mutaxassislik fani 
To'g'ri javoblar soni: {fanlar[2][0]} ta  
Ball: {fanlar[2][1]}
_______
✅ <b>Umumiy ball:</b> {umumiy_ball}
⏰ {vaqt}


<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>
"""

        # Keshga saqlash
        save_to_cache(user_id, matn)
        return matn

    except Exception as e:
        logging.exception(f"❌ Xatolik ID {user_id} uchun:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    finally:
        try:
            driver.quit()
        except:
            pass


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


@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()

    # Avval keshdan tekshirish
    cached_result = get_from_cache(abt_id)
    if cached_result:
        await msg.answer(cached_result, parse_mode="HTML")
        return

    # Loading message
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)

        # Loading message o'chirish
        await loading_msg.delete()

        if result.startswith("❌"):
            await msg.answer(
                f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. Siz hozirda natijaga buyurtma berishingiz mumkin.</b>",
                parse_mode="HTML")
        else:
            await msg.answer(result, parse_mode="HTML")

    except Exception as e:
        logging.exception("❌ Ichki xatolik:")
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