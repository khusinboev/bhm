import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, List

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
from contextlib import contextmanager

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

os.makedirs("screens", exist_ok=True)

data_router = Router()

class MainState2(StatesGroup):
    natija = State()

# Global WebDriver pool
class WebDriverPool:
    def __init__(self, max_size: int = 3):
        self.pool = asyncio.Queue(maxsize=max_size)
        self.max_size = max_size
        self._initialized = False
    
    async def initialize(self):
        if self._initialized:
            return
        
        for _ in range(self.max_size):
            driver = self._create_driver()
            await self.pool.put(driver)
        self._initialized = True
    
    def _create_driver(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-logging")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        return webdriver.Chrome(options=options)
    
    @contextmanager
    def get_driver(self):
        try:
            driver = self.pool.get_nowait()
        except asyncio.QueueEmpty:
            driver = self._create_driver()
        
        try:
            yield driver
        finally:
            try:
                # Driver-ni reset qilish
                driver.delete_all_cookies()
                driver.get("about:blank")
                self.pool.put_nowait(driver)
            except:
                driver.quit()
                # Yangi driver yaratish
                try:
                    new_driver = self._create_driver()
                    self.pool.put_nowait(new_driver)
                except:
                    pass

# Global pool instance
driver_pool = WebDriverPool()

# Cache mexanizmi
class ResultCache:
    def __init__(self, ttl: int = 300):  # 5 daqiqa
        self.cache = {}
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            result, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return result
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, value: str):
        self.cache[key] = (value, time.time())

result_cache = ResultCache()

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

executor = ThreadPoolExecutor(max_workers=3)

def extract_scores_from_html(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    """HTML dan balllarni ajratib olish"""
    card_headers = soup.select("div.card-header.card-div.text-center")
    fanlar = []
    
    for header in card_headers:
        text = header.get_text(strip=True)
        if "To'g'ri javoblar soni" in text:
            bolds = header.find_all("b")
            if len(bolds) == 2:
                correct = bolds[0].text.strip()
                score = bolds[1].text.strip()
                fanlar.append((correct, score))
            if len(fanlar) >= 3:
                break
    
    return fanlar

def extract_total_score(soup: BeautifulSoup) -> str:
    """Umumiy ballni ajratib olish"""
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

def format_result_message(fio: str, user_id: str, fanlar: List[Tuple[str, str]], 
                         umumiy_ball: str) -> str:
    """Natija xabarini formatlash"""
    vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
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

def get_abiturient_info_by_id(user_id: str) -> str:
    """Abiturient ma'lumotlarini olish (optimallashtirilgan)"""
    
    # Cache-dan tekshirish
    cached_result = result_cache.get(user_id)
    if cached_result:
        return cached_result
    
    try:
        with driver_pool.get_driver() as driver:
            print(f"🌐 Saytga kirilmoqda... ID: {user_id}")
            
            # Sahifaga borish
            driver.get("https://mandat.uzbmb.uz/")
            wait = WebDriverWait(driver, 20)
            
            # Sahifa yuklashini kutish
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            
            # ID kiritish
            input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
            input_field.clear()
            input_field.send_keys(str(user_id))
            
            # Qidiruv tugmasi
            search_btn = wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))
            driver.execute_script("arguments[0].click();", search_btn)
            print("🔍 Qidiruv bosildi")
            
            # Detali tugmasi
            time.sleep(1)
            detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
            driver.execute_script("arguments[0].click();", detail_btn)
            print("📄 Batafsil sahifaga o'tildi")
            
            # Sahifa yuklanishini kutish
            time.sleep(1)
            
            # FIO olish
            fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
            fio = fio_element.text.strip()
            
            # HTML parsing
            soup = BeautifulSoup(driver.page_source, "html.parser")
            
            # Ma'lumotlarni ajratish
            fanlar = extract_scores_from_html(soup)
            umumiy_ball = extract_total_score(soup)
            
            # Natija formatlash
            result = format_result_message(fio, user_id, fanlar, umumiy_ball)
            
            # Cache-ga saqlash
            result_cache.set(user_id, result)
            
            return result
            
    except Exception as e:
        logging.exception(f"❌ Xatolik ID {user_id} uchun:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

async def check_membership(user_id: int) -> Tuple[bool, Optional[List]]:
    """A'zolik tekshirish"""
    try:
        return await CheckData.check_member(bot, user_id)
    except Exception as e:
        logging.exception("A'zolik tekshirishda xatolik:")
        return False, None

@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    """ID orqali ma'lumot olish (optimallashtirilgan)"""
    user_id = msg.from_user.id
    
    # A'zolik tekshirish
    check_status, channels = await check_membership(user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                        reply_markup=await CheckData.channels_btn(channels))
        return
    
    abt_id = msg.text.strip()
    
    # Loading message
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")
    
    try:
        # Pool-ni initialize qilish
        await driver_pool.initialize()
        
        # Async task yaratish
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
        
        # Loading message-ni o'chirish
        try:
            await loading_msg.delete()
        except:
            pass
        
        if result.startswith("❌"):
            await msg.answer(f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. Siz hozirda natijaga buyurtma berishingiz mumkin.</b>", 
                           parse_mode="HTML")
        else:
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
    """Noto'g'ri format uchun"""
    from_chat_id = "@Second_Polat"
    message_id = 733
    await bot.copy_message(
        chat_id=msg.chat.id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_markup=await UserPanels.to_back(),
    )