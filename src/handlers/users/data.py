import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, List
from functools import lru_cache

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

os.makedirs("screens", exist_ok=True)

data_router = Router()

class MainState2(StatesGroup):
    natija = State()

# ThreadPoolExecutor'ni faqat bitta marta yaratish
executor = ThreadPoolExecutor(max_workers=3)

# Kesh uchun
result_cache = {}
CACHE_EXPIRY = 300  # 5 daqiqa

class DriverPool:
    def __init__(self, pool_size=2):
        self.pool = []
        self.pool_size = pool_size
        self.lock = asyncio.Lock()
        self.options = self._create_options()
    
    def _create_options(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--memory-pressure-off")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        return options
    
    def get_driver(self):
        if self.pool:
            return self.pool.pop()
        return webdriver.Chrome(options=self.options)
    
    def return_driver(self, driver):
        if len(self.pool) < self.pool_size:
            self.pool.append(driver)
        else:
            try:
                driver.quit()
            except:
                pass

driver_pool = DriverPool()

@lru_cache(maxsize=1000)
def format_result_text(fio: str, user_id: str, fanlar: tuple, umumiy_ball: str, vaqt: str) -> str:
    """Ma'lumotlarni formatlash (kesh bilan)"""
    return f"""<b>BAKALAVR 2025</b>
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

def extract_fanlar_info(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    """Fan ma'lumotlarini ajratib olish - asl kodingizga moslab"""
    card_headers = soup.select("div.card-header.card-div.text-center")
    fanlar = []
    for header in card_headers:
        text = header.get_text(strip=True)
        if "To'g'ri javoblar soni" in text or "To'g'ri javoblar soni" in text:
            bolds = header.find_all("b")
            if len(bolds) == 2:
                correct = bolds[0].text.strip()
                score = bolds[1].text.strip()
                fanlar.append((correct, score))
            if len(fanlar) >= 3:
                break
    return fanlar

def extract_umumiy_ball(soup: BeautifulSoup) -> str:
    """Umumiy ballni ajratib olish - asl kodingizga moslab"""
    umumiy_ball = "?"
    umumiy_div = soup.find("div", class_="card-header card-div text-center", 
                          string=lambda t: t and "Umumiy ball" in t)
    if not umumiy_div:
        umumiy_div = soup.find("div", class_="bg-success")
    if umumiy_div:
        umumiy_b = umumiy_div.find("b")
        if umumiy_b:
            umumiy_ball = umumiy_b.text.strip()
    return umumiy_ball

def get_abiturient_info_by_id(user_id: str) -> str:
    """Asl kodingizga asoslangan optimallashtirish"""
    driver = driver_pool.get_driver()
    try:
        print(f"🌐 Saytga kirilmoqda... ID: {user_id}")
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1.5)

        driver.execute_script("document.getElementById('SearchBtn1').click();")
        print(f"🔍 Qidiruv bosildi - ID: {user_id}")

        time.sleep(1)
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()
        print(f"📄 Batafsil sahifaga o'tildi - ID: {user_id}")

        # Sahifa yuklanishini kutish
        time.sleep(1)

        # FIO olish
        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        # Sahifa HTML
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Fanlar ma'lumotlarini ajratib olish
        fanlar = extract_fanlar_info(soup)

        # Umumiy ball olish
        umumiy_ball = extract_umumiy_ball(soup)

        # Vaqt
        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Natija yaratish
        if len(fanlar) >= 3:
            return format_result_text(fio, user_id, tuple(fanlar), umumiy_ball, vaqt)
        else:
            return f"❌ ID: {user_id} uchun to'liq ma'lumot topilmadi."

    except Exception as e:
        logging.exception(f"❌ Xatolik ID {user_id} uchun:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    finally:
        driver_pool.return_driver(driver)

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
    
    # Kanal tekshirish
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return
    
    abt_id = msg.text.strip()
    current_time = time.time()
    
    # Keshdan tekshirish
    if abt_id in result_cache:
        cached_result, cached_time = result_cache[abt_id]
        if current_time - cached_time < CACHE_EXPIRY:
            await msg.answer(cached_result, parse_mode="HTML")
            return
    
    # Loading message
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")
    
    try:
        # Asinxron tarzda ma'lumot olish (sinxron funksiyani thread'da ishga tushirish)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
        
        # Loading message'ni o'chirish
        try:
            await loading_msg.delete()
        except:
            pass
        
        if result.startswith("❌"):
            await msg.answer(f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. Siz hozirda natijaga buyurtma berishingiz mumkin.</b>", 
                           parse_mode="HTML")
        else:
            # Keshga saqlash
            result_cache[abt_id] = (result, current_time)
            await msg.answer(result, parse_mode="HTML")
            
    except Exception as e:
        logging.exception("❌ Ichki xatolik:")
        try:
            await loading_msg.delete()
        except:
            pass
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