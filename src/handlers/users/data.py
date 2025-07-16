import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from bs4 import BeautifulSoup
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import os

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

executor = ThreadPoolExecutor()

# Ishga tushirish uchun kerakli papkani yaratish
os.makedirs("screens", exist_ok=True)

data_router = Router()

class MainState2(StatesGroup):
    natija = State()

# Funksiyalarni modullashtirish va optimallashtirish
def setup_chrome_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)

def parse_fanlar(soup):
    fanlar = []
    headers = soup.select("div.card-header.card-div.text-center")
    for header in headers:
        text = header.get_text(strip=True)
        if "To’g’ri javoblar soni" in text or "To'g'ri javoblar soni" in text:
            bolds = header.find_all("b")
            if len(bolds) == 2:
                fanlar.append((bolds[0].text.strip(), bolds[1].text.strip()))
            if len(fanlar) >= 3:
                break
    return fanlar

def parse_umumiy_ball(soup):
    umumiy_div = soup.find("div", class_="card-header card-div text-center", string=lambda t: t and "Umumiy ball" in t)
    if not umumiy_div:
        umumiy_div = soup.find("div", class_="bg-success")
    return umumiy_div.find("b").text.strip() if umumiy_div and umumiy_div.find("b") else "?"

def get_abiturient_info_by_id(user_id: str):
    driver = setup_chrome_driver()
    try:
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # ID kiritish va qidirish
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))

        # Qidiruvni boshlash
        driver.execute_script("document.getElementById('SearchBtn1').click();")

        # Batafsil sahifaga o'tish
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()

        # FIO olish
        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        # Sahifa HTML'ni o‘qib olish
        soup = BeautifulSoup(driver.page_source, "html.parser")
        fanlar = parse_fanlar(soup)
        umumiy_ball = parse_umumiy_ball(soup)
        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Natijani yuborish
        return f"""<b>BAKALAVR 2025</b>
_______
<b>FIO</b>:  {fio}
🆔:  <b>{user_id}</b>
_______
1️⃣ Majburiy fanlar 
To‘g‘ri javoblar soni: {fanlar[0][0]} ta  
Ball: {fanlar[0][1]}

2️⃣ 1-mutaxassislik fani 
To‘g‘ri javoblar soni: {fanlar[1][0]} ta  
Ball: {fanlar[1][1]}

3️⃣ 2-mutaxassislik fani 
To‘g‘ri javoblar soni: {fanlar[2][0]} ta  
Ball: {fanlar[2][1]}
_______
✅ <b>Umumiy ball:</b> {umumiy_ball}
⏰ {vaqt}

<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>
"""

    except Exception as e:
        logging.exception("❌ Xatolik:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring."

    finally:
        driver.quit()


# === HANDLER: ID qabul qilib, fon threadda ishlatish ===
@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:", reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()
    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    async def process_and_reply():
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            if result.startswith("❌"):
                await msg.answer(f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. Siz hozirda natijaga buyurtma berishingiz mumkin .</b>", parse_mode="HTML")
            else:
                await msg.answer(result, parse_mode="HTML")
        except Exception as e:
            logging.exception("❌ Ichki xatolik:")
            await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring.")

    asyncio.create_task(process_and_reply())

# === Handler 2: Qolgan barcha xabarlar uchun ===
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