import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

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


@data_router.message(MainState2.natija, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    try:
        await state.clear()
    except: pass


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


# ThreadPoolExecutor worker sonini cheklash
executor = ThreadPoolExecutor(max_workers=3)

# Sodda cache - faqat oxirgi 100 ta natija uchun
simple_cache = {}
MAX_CACHE_SIZE = 100

def get_chrome_options():
    """Chrome options-ni alohida funksiyaga ajratish"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-logging")  # Log-larni kamaytirish
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )
    return options

def get_abiturient_info_by_id(user_id: str):
    # Sodda cache tekshirish
    cache_key = f"{user_id}_{int(time.time() // 300)}"  # 5 daqiqa cache
    if cache_key in simple_cache:
        return simple_cache[cache_key]
    
    driver = webdriver.Chrome(options=get_chrome_options())
    try:
        print("🌐 Saytga kirilmoqda...")
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1.5)

        driver.execute_script("document.getElementById('SearchBtn1').click();")
        print("🔍 Qidiruv bosildi")

        time.sleep(1)
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()
        print("📄 Batafsil sahifaga o'tildi")

        # Sahifa yuklanishini kutish
        time.sleep(1)

        # FIO olish
        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()

        # Sahifa HTML
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Faqat kerakli 3 ta ball bloklarini olish
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
                    break  # faqat 3 ta blok yetarli

        # Umumiy ball olish
        umumiy_ball = "?"
        umumiy_div = soup.find("div", class_="card-header card-div text-center", string=lambda t: t and "Umumiy ball" in t)
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
        
        # Cache-ga saqlash
        if len(simple_cache) >= MAX_CACHE_SIZE:
            # Eng eski elementni o'chirish
            oldest_key = min(simple_cache.keys())
            del simple_cache[oldest_key]
        simple_cache[cache_key] = matn
        
        return matn

    except Exception as e:
        logging.exception("❌ Xatolik:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    finally:
        driver.quit()

# === HANDLER: ID qabul qilib, fon threadda ishlatish ===
@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                             reply_markup=await CheckData.channels_btn(channels))
        return
    
    abt_id = msg.text.strip()
    
    # Loading message qo'shish
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    async def process_and_reply():
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            
            # Loading message-ni o'chirish
            try:
                await loading_msg.delete()
            except:
                pass
            
            if result.startswith("❌"):
                await msg.answer(f"<b>ID: {abt_id} ma'lumotlari topilmadi. Hali natijangiz chiqmagan ko'rinadi. Siz hozirda natijaga buyurtma berishingiz mumkin .</b>", parse_mode="HTML")
            else:
                await msg.answer(result, parse_mode="HTML")
        except Exception as e:
            logging.exception("❌ Ichki xatolik:")
            try:
                await loading_msg.delete()
            except:
                pass
            await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")

    asyncio.create_task(process_and_reply())


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