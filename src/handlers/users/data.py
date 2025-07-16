import logging
import asyncio
from concurrent.futures import ProcessPoolExecutor
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
import os

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

# Global sozlamalar
data_router = Router()
os.makedirs("screens", exist_ok=True)
request_queue = asyncio.Queue()
executor = ProcessPoolExecutor(max_workers=2)


class MainState2(StatesGroup):
    natija = State()


def get_abiturient_info_by_id(user_id: str):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--single-process")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    try:
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        driver.execute_script("document.getElementById('SearchBtn1').click();")

        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info"))).click()

        fio = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b"))).text.strip()
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        card_headers = soup.select("div.card-header.card-div.text-center")
        fanlar = []
        for header in card_headers:
            text = header.get_text(strip=True)
            if "To’g’ri javoblar soni" in text or "To'g'ri javoblar soni" in text:
                bolds = header.find_all("b")
                if len(bolds) == 2:
                    fanlar.append((bolds[0].text.strip(), bolds[1].text.strip()))
                if len(fanlar) >= 3:
                    break

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

<b>✅ Ma'lumotlar @mandat_uzbmbbot tomonidan olindi</b>"""
    except Exception as e:
        logging.exception("❌ Xatolik:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring."
    finally:
        driver.quit()


@data_router.message(MainState2.natija, F.text.regexp(r"^\\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id(msg: Message):
    await msg.answer("⏳ Navbatga qo‘shildingiz. Kuting...")
    await request_queue.put(msg)


async def process_queue():
    while True:
        msg = await request_queue.get()
        user_id = msg.from_user.id
        abt_id = msg.text.strip()
        try:
            check_status, channels = await CheckData.check_member(bot, user_id)
            if not check_status:
                await msg.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
                                 reply_markup=await CheckData.channels_btn(channels))
                continue
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            await msg.answer(result, parse_mode="HTML")
        except Exception as e:
            await msg.answer("❌ Xatolik yuz berdi. Keyinroq urinib ko‘ring.")
        request_queue.task_done()


@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def ask_for_id(message: Message, state: FSMContext):
    from_chat_id = "@Second_Polat"
    message_id = 733
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_markup=await UserPanels.to_back(),
    )
    await state.set_state(MainState2.natija)


@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def go_back(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    await state.clear()


# Bot start paytida navbatni ishga tushirish uchun
async def on_startup(dispatcher):
    asyncio.create_task(process_queue())