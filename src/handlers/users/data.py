import logging
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

# Constants
RESULT_CHANNEL_ID = "@Second_Polat"
RESULT_MESSAGE_ID = 733
SCREENSHOTS_DIR = "screens"
CHROME_OPTIONS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--disable-extensions",
    "--disable-blink-features=AutomationControlled",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
]

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

data_router = Router()
executor = ThreadPoolExecutor()

class MainState2(StatesGroup):
    natija = State()

async def clear_state(state: FSMContext) -> None:
    """Clear the current state if possible."""
    try:
        await state.clear()
    except Exception:
        pass

def configure_chrome_options() -> Options:
    """Configure and return Chrome options."""
    options = Options()
    for option in CHROME_OPTIONS:
        options.add_argument(option)
    return options

def extract_fanlar_data(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    """Extract fanlar data from the BeautifulSoup object."""
    fanlar = []
    card_headers = soup.select("div.card-header.card-div.text-center")
    
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
    """Extract umumiy ball from the BeautifulSoup object."""
    umumiy_div = soup.find("div", class_="card-header card-div text-center", 
                          string=lambda t: t and "Umumiy ball" in t)
    if not umumiy_div:
        umumiy_div = soup.find("div", class_="bg-success")
    
    if umumiy_div:
        umumiy_b = umumiy_div.find("b")
        if umumiy_b:
            return umumiy_b.text.strip()
    return "?"

def get_abiturient_info_by_id(user_id: str) -> str:
    """Fetch abiturient information by ID from mandat.uzbmb.uz."""
    options = configure_chrome_options()
    driver = webdriver.Chrome(options=options)
    
    try:
        logging.info(f"🌐 Opening website for ID: {user_id}")
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # Input ID and search
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        asyncio.sleep(1.5)
        driver.execute_script("document.getElementById('SearchBtn1').click();")
        logging.info("🔍 Search initiated")

        # Go to details page
        asyncio.sleep(1)
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()
        logging.info("📄 Navigated to details page")

        # Wait for page to load
        asyncio.sleep(1)

        # Get FIO
        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        # Parse page content
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        fanlar = extract_fanlar_data(soup)
        umumiy_ball = extract_umumiy_ball(soup)
        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if len(fanlar) < 3:
            raise ValueError("Not enough fan data found")

        result_text = f"""<b>BAKALAVR 2025</b>
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
        return result_text

    except Exception as e:
        logging.exception(f"❌ Error processing ID {user_id}:")
        return f"❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring. ID: {user_id}"

    finally:
        driver.quit()

@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def back_to_main_menu(message: Message, state: FSMContext):
    """Handle back button press."""
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    await clear_state(state)

@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_results(message: Message, state: FSMContext):
    """Show results menu."""
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=RESULT_CHANNEL_ID,
        message_id=RESULT_MESSAGE_ID,
        reply_markup=await UserPanels.to_back(),
    )
    await state.set_state(MainState2.natija)

@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    """Handle ID query and fetch results."""
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                       reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()
    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    async def process_and_reply():
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            
            if result.startswith("❌"):
                await msg.answer(
                    f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. "
                    "Siz hozirda natijaga buyurtma berishingiz mumkin.</b>",
                    parse_mode="HTML"
                )
            else:
                await msg.answer(result, parse_mode="HTML")
                
        except Exception as e:
            logging.exception("❌ Internal error processing request:")
            await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

    asyncio.create_task(process_and_reply())

@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def handle_other_messages(msg: Message):
    """Handle other messages in natija state by showing the results menu again."""
    await bot.copy_message(
        chat_id=msg.chat.id,
        from_chat_id=RESULT_CHANNEL_ID,
        message_id=RESULT_MESSAGE_ID,
        reply_markup=await UserPanels.to_back(),
    )