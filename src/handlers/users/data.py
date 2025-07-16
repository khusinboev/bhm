import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from selenium.common.exceptions import TimeoutException, WebDriverException
from datetime import datetime
import os
import time

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

data_router = Router()

class MainState2(StatesGroup):
    natija = State()

@dataclass
class AbiturientInfo:
    """Abiturient ma'lumotlari uchun dataclass"""
    fio: str
    user_id: str
    fanlar: List[Tuple[str, str]]
    umumiy_ball: str
    vaqt: str

class BrowserManager:
    """Browser boshqarish uchun singleton class"""
    _instance = None
    _browser = None
    _wait = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._browser is None:
            self._init_browser()
    
    def _init_browser(self):
        """Browser sozlamalarini optimallash"""
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-images")  # Rasmlarni yuklamaslik
        options.add_argument("--disable-javascript")  # JS ni o'chirish (agar kerak bo'lmasa)
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-features=TranslateUI")
        options.add_argument("--disable-ipc-flooding-protection")
        options.add_argument("--memory-pressure-off")
        options.add_argument("--max_old_space_size=4096")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        
        # Prefs qo'shish
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_settings.popups": 0,
            "profile.managed_default_content_settings.media_stream": 2,
        }
        options.add_experimental_option("prefs", prefs)
        
        try:
            self._browser = webdriver.Chrome(options=options)
            self._wait = WebDriverWait(self._browser, 20)  # Timeout ni kamaytirish
            logger.info("✅ Browser muvaffaqiyatli ishga tushdi")
        except Exception as e:
            logger.error(f"❌ Browser ishga tushurishda xatolik: {e}")
            raise
    
    @property
    def browser(self):
        return self._browser
    
    @property
    def wait(self):
        return self._wait
    
    def restart_browser(self):
        """Browser ni qayta ishga tushirish"""
        try:
            if self._browser:
                self._browser.quit()
            self._init_browser()
            logger.info("🔄 Browser qayta ishga tushdi")
        except Exception as e:
            logger.error(f"❌ Browser qayta ishga tushirishda xatolik: {e}")

# Global browser manager
browser_manager = BrowserManager()

class AbiturientParser:
    """Abiturient ma'lumotlarini parse qilish uchun class"""
    
    @staticmethod
    def parse_subject_scores(soup: BeautifulSoup) -> List[Tuple[str, str]]:
        """Fanlar bo'yicha balllarni parse qilish"""
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
        
        # Agar 3 ta fan topilmasa, bo'sh qiymatlar bilan to'ldirish
        while len(fanlar) < 3:
            fanlar.append(("?", "?"))
        
        return fanlar
    
    @staticmethod
    def parse_total_score(soup: BeautifulSoup) -> str:
        """Umumiy ballni parse qilish"""
        # Bir nechta usulda umumiy ballni topishga urinish
        selectors = [
            "div.card-header.card-div.text-center",
            "div.bg-success",
            "div[class*='success']",
            "div[class*='total']"
        ]
        
        for selector in selectors:
            elements = soup.select(selector)
            for element in elements:
                text = element.get_text(strip=True)
                if "Umumiy ball" in text:
                    b_tag = element.find("b")
                    if b_tag:
                        return b_tag.text.strip()
        
        return "?"
    
    @staticmethod
    def format_result(info: AbiturientInfo) -> str:
        """Natijani formatlash"""
        return f"""<b>BAKALAVR 2025</b>
_______
<b>FIO</b>:  {info.fio}
🆔:  <b>{info.user_id}</b>
_______
1️⃣ Majburiy fanlar 
To'g'ri javoblar soni: {info.fanlar[0][0]} ta  
Ball: {info.fanlar[0][1]}

2️⃣ 1-mutaxassislik fani 
To'g'ri javoblar soni: {info.fanlar[1][0]} ta  
Ball: {info.fanlar[1][1]}

3️⃣ 2-mutaxassislik fani 
To'g'ri javoblar soni: {info.fanlar[2][0]} ta  
Ball: {info.fanlar[2][1]}
_______
✅ <b>Umumiy ball:</b> {info.umumiy_ball}
⏰ {info.vaqt}


<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>
"""

def get_abiturient_info_by_id(user_id: str) -> str:
    """Abiturient ma'lumotlarini olish (optimallashtirilgan)"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            logger.info(f"🌐 Urinish {attempt + 1}/{max_retries}: Saytga kirish...")
            
            browser = browser_manager.browser
            wait = browser_manager.wait
            
            # Saytga kirish
            browser.get("https://mandat.uzbmb.uz/")
            
            # Sahifa yuklanganimga kutish
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            
            # ID kiritish
            input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
            input_field.clear()
            input_field.send_keys(str(user_id))
            
            # Input qiymat kiritilganimga kutish
            wait.until(lambda d: input_field.get_attribute("value") == str(user_id))
            
            # Qidirish tugmasini bosish
            search_btn = wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))
            search_btn.click()
            logger.info("🔍 Qidiruv boshlandi")
            
            # Natija yuklanishini kutish
            time.sleep(2)
            
            # Batafsil tugmasini topish va bosish
            detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
            detail_btn.click()
            logger.info("📄 Batafsil sahifaga o'tish")
            
            # Sahifa to'liq yuklanishini kutish
            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            time.sleep(3)  # Qo'shimcha kutish
            
            # FIO olish
            fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
            fio = fio_element.text.strip()
            
            # HTML ni olish va parse qilish
            html = browser.page_source
            soup = BeautifulSoup(html, "html.parser")
            
            # Ma'lumotlarni parse qilish
            fanlar = AbiturientParser.parse_subject_scores(soup)
            umumiy_ball = AbiturientParser.parse_total_score(soup)
            vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # AbiturientInfo obyekti yaratish
            info = AbiturientInfo(
                fio=fio,
                user_id=user_id,
                fanlar=fanlar,
                umumiy_ball=umumiy_ball,
                vaqt=vaqt
            )
            
            result = AbiturientParser.format_result(info)
            logger.info(f"✅ Ma'lumotlar muvaffaqiyatli olindi: {user_id}")
            return result
            
        except TimeoutException as e:
            logger.warning(f"⏰ Timeout xatoligi (urinish {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            return f"❌ Vaqt tugashi: Sahifa juda sekin yuklandi. Keyinroq urinib ko'ring."
            
        except WebDriverException as e:
            logger.error(f"🌐 WebDriver xatoligi (urinish {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                # Browser ni qayta ishga tushirish
                browser_manager.restart_browser()
                time.sleep(retry_delay)
                continue
            return f"❌ Browser xatoligi: {str(e)}"
            
        except Exception as e:
            logger.exception(f"❌ Umumiy xatolik (urinish {attempt + 1}):")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            return f"❌ Xatolik yuz berdi: {str(e)}"
    
    return "❌ Barcha urinishlar muvaffaqiyatsiz tugadi"

# ThreadPoolExecutor optimallash
executor = ThreadPoolExecutor(max_workers=2)  # Worker soni kamaytirish

@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    """Ortga tugmasi"""
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    await state.clear()

@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    """Natija bo'limiga kirish"""
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
    """ID so'rovi ishlov berish"""
    user_id = msg.from_user.id
    
    # Kanalga a'zolik tekshirish
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer(
            "❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
            reply_markup=await CheckData.channels_btn(channels)
        )
        return
    
    abt_id = msg.text.strip()
    
    # Loading xabari
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")
    
    try:
        # Asinxron ravishda ma'lumotlarni olish
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
        
        # Loading xabarini o'chirish
        await loading_msg.delete()
        
        if result.startswith("❌"):
            await msg.answer(
                f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. "
                f"Siz hozirda natijaga buyurtma berishingiz mumkin.</b>",
                parse_mode="HTML"
            )
        else:
            await msg.answer(result, parse_mode="HTML")
            
    except Exception as e:
        logger.exception("❌ Ichki xatolik:")
        await loading_msg.delete()
        await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def handle_invalid_input(msg: Message):
    """Noto'g'ri kiritilgan ma'lumotlar"""
    from_chat_id = "@Second_Polat"
    message_id = 733
    
    await bot.copy_message(
        chat_id=msg.chat.id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_markup=await UserPanels.to_back(),
    )

# Graceful shutdown
async def shutdown_browser():
    """Dastur to'xtatilganda browser ni yopish"""
    try:
        if browser_manager._browser:
            browser_manager._browser.quit()
            logger.info("🔴 Browser yopildi")
    except Exception as e:
        logger.error(f"❌ Browser yopishda xatolik: {e}")

# Agar kerak bo'lsa, bu funksiyani main.py da chaqirish mumkin
# import atexit
# atexit.register(lambda: asyncio.run(shutdown_browser()))