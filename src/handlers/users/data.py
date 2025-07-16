import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import os
os.makedirs("screens", exist_ok=True)

data_router = Router()
executor = ThreadPoolExecutor()

def get_ball_by_label(label: str, driver: webdriver):
    try:
        return driver.find_element(
            By.XPATH, f"//div[contains(text(),'{label}')]/following-sibling::b"
        ).text.strip()
    except:
        return "?"

def get_abiturient_info_by_id(user_id: str):
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

    driver = webdriver.Chrome(options=options)
    try:
        print("🌐 Saytga kirilmoqda...")
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # Screenshot 1: Sahifa to'liq yuklangan
        screenshot1 = f"screens/screen_ready_{int(time.time())}.png"
        driver.save_screenshot(screenshot1)

        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1.5)

        # Screenshot 2: ID kiritilgan
        screenshot2 = f"screens/screen_id_entered_{int(time.time())}.png"
        driver.save_screenshot(screenshot2)

        driver.execute_script("document.getElementById('SearchBtn1').click();")
        print("🔍 Qidiruv bosildi")

        # Screenshot 3: Qidiruvdan keyin
        time.sleep(2)
        screenshot3 = f"screens/screen_after_search_{int(time.time())}.png"
        driver.save_screenshot(screenshot3)

        # Batafsil tugmasini bosish
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()
        print("📄 Batafsil sahifaga o‘tildi")

        # Screenshot 4: Batafsil ochilgan
        time.sleep(2)
        screenshot4 = f"screens/screen_detail_opened_{int(time.time())}.png"
        driver.save_screenshot(screenshot4)

        fio_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'F.I.SH')]/b")))
        fio = fio_element.text.strip()
        print(fio)
        # Ballar va javoblar (6ta)
        card_divs = wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div.card-header.card-div.text-center"))
        )

        fanlar = []

        for card in card_divs[:6]:
            # card ning ichki HTML'ni olish
            html = card.get_attribute("innerHTML")
            soup = BeautifulSoup(html, "html.parser")

            bolds = soup.find_all("b")
            print(bolds)
            if len(bolds) >= 2:
                correct = bolds[0].text.strip()
                score = bolds[1].text.strip()
                fanlar.append((correct, score))
            else:
                fanlar.append(("?", "?"))

        # Qolgan ballar
        imtiyoz = get_ball_by_label("Imtiyoz ball", driver)
        ijodiy = get_ball_by_label("Ijodiy ball", driver)
        cefr = get_ball_by_label("CEFR ball", driver)
        milliy = get_ball_by_label("Milliy sertifikat", driver)
        umumiy = get_ball_by_label("Umumiy ball", driver)

        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        matn = f"""<b>BAKALAVR 2025</b>
___________________________________
<b>FIO</b>:  {fio}
___________________________________
🆔:  <b>{user_id}</b>
<b>Ta'lim tili</b>:  O'zbekcha
___________________________________
1️⃣ Ona tili 
10 ta savol:  {fanlar[0][1]} ball 
({fanlar[0][0]} ta to'g'ri javob)

2️⃣ Matematika 
10 ta savol:  {fanlar[1][1]} ball 
({fanlar[1][0]} ta to'g'ri javob)

3️⃣ Tarix 
10 ta savol:  {fanlar[2][1]} ball 
({fanlar[2][0]} ta to'g'ri javob)

4️⃣ Tarix 
30 ta savol:  {fanlar[3][1]} ball  
({fanlar[3][0]} ta to'g'ri javob)

5️⃣ Ona tili va adabiyot 
30 ta savol:  {fanlar[4][1]} ball  
({fanlar[4][0]} ta to'g'ri javob)

6️⃣ Chet tili (yoki qo‘shimcha) 
30 ta savol:  {fanlar[5][1]} ball  
({fanlar[5][0]} ta to'g'ri javob)
___________________________________
🔹 Chet tili  sertifikati:  {cefr} ball
🔹 Ijodiy ball:  {ijodiy} ball
🔹 Umumta'lim fan sertifikati:  {milliy} ball
🔹 Imtiyoz ball:  {imtiyoz} ball
___________________________________

🔸<b>UMUMIY</b>:  {umumiy} ball
___________________________________
⏰ {vaqt}
"""
        return matn

    except Exception as e:
        logging.exception("❌ Xatolik:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring."

    finally:
        driver.quit()


# === HANDLER: ID qabul qilib, fon threadda ishlatish ===
@data_router.message(F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    abt_id = msg.text.strip()
    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    async def process_and_reply():
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(executor, get_abiturient_info_by_id, abt_id)
            if result.startswith("❌"):
                await msg.answer(f"🚫 <b>ID: {abt_id}</b> uchun ma'lumot topilmadi.", parse_mode="HTML")
            else:
                await msg.answer(result, parse_mode="HTML")
        except Exception as e:
            logging.exception("❌ Ichki xatolik:")
            await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring.")

    asyncio.create_task(process_and_reply())