import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

# Log konfiguratsiyasi
logging.basicConfig(level=logging.INFO)

data_router = Router()
executor = ThreadPoolExecutor()

class MainState2(StatesGroup):
    natija = State()

# Global driver
driver = None

def init_driver():
    global driver
    if driver is None:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        )
        driver = webdriver.Chrome(options=chrome_options)

def close_driver():
    global driver
    if driver:
        driver.quit()
        driver = None

def get_abiturient_info_by_id(user_id: str):
    try:
        init_driver()
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 15)

        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(user_id)

        search_btn = wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))
        search_btn.click()

        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()

        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        fanlar = []
        card_headers = soup.select("div.card-header.card-div.text-center")
        for header in card_headers:
            text = header.get_text(strip=True)
            if "To’g’ri javoblar soni" in text or "To'g'ri javoblar soni" in text:
                bolds = header.find_all("b")
                if len(bolds) == 2:
                    correct = bolds[0].text.strip()
                    score = bolds[1].text.strip()
                    fanlar.append((correct, score))
                if len(fanlar) >= 3:
                    break

        if len(fanlar) < 3:
            return "❌ Ma'lumotlar to‘liq topilmadi. Ehtimol natija hali e'lon qilinmagan."

        umumiy_ball = "?"
        umumiy_div = soup.find("div", class_="card-header card-div text-center", string=lambda t: t and "Umumiy ball" in t)
        if not umumiy_div:
            umumiy_div = soup.find("div", class_="bg-success")
        if umumiy_div:
            umumiy_b = umumiy_div.find("b")
            if umumiy_b:
                umumiy_ball = umumiy_b.text.strip()

        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""<b>BAKALAVR 2025</b>
___________________________________
<b>FIO</b>:  {fio}
🆔:  <b>{user_id}</b>
___________________________________
1️⃣ Majburiy fanlar 
To‘g‘ri javoblar soni: {fanlar[0][0]} ta  
Ball: {fanlar[0][1]}

2️⃣ 1-mutaxassislik fani 
To‘g‘ri javoblar soni: {fanlar[1][0]} ta  
Ball: {fanlar[1][1]}

3️⃣ 2-mutaxassislik fani 
To‘g‘ri javoblar soni: {fanlar[2][0]} ta  
Ball: {fanlar[2][1]}
___________________________________
✅ <b>Umumiy ball:</b> {umumiy_ball}
⏰ {vaqt}

<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>
"""

    except Exception as e:
        logging.exception("❌ Xatolik yuz berdi:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring."

# === BOSH MENUGA QAYTISH ===
@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders_back(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    await state.clear()

# === NATIJA SO‘RASH ===
@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_natija_panel(message: Message, state: FSMContext):
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id="@Second_Polat",
        message_id=733,
        reply_markup=await UserPanels.to_back(),
    )
    await state.set_state(MainState2.natija)

# === ID YUBORILGANDAGI NATIJA KODI ===
@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:", reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()
    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    async def process():
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            if result.startswith("❌"):
                await msg.answer(f"<b>ID: {abt_id} ma'lumotlari topilmadi yoki hali natija chiqmagan.</b>", parse_mode="HTML")
            else:
                await msg.answer(result, parse_mode="HTML")
        except Exception as e:
            logging.exception("❌ Ichki xatolik:")
            await msg.answer("❌ Ichki xatolik yuz berdi. Keyinroq urinib ko‘ring.")

    asyncio.create_task(process())

# === BOSHQA XABARLAR ===
@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def handle_unexpected_text(msg: Message):
    await bot.copy_message(
        chat_id=msg.chat.id,
        from_chat_id="@Second_Polat",
        message_id=733,
        reply_markup=await UserPanels.to_back(),
    )