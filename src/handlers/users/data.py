import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import os
os.makedirs("screens", exist_ok=True)

data_router = Router()
executor = ThreadPoolExecutor()


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
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # ID ni kiritish
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1)

        # Qidiruv bosish
        driver.execute_script("document.getElementById('SearchBtn1').click();")
        time.sleep(2)

        # Batafsil tugmasi
        detail_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_btn.click()

        # ❗ Sahifa to‘liq yuklanguncha kutamiz
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

        # 🔁 Yangi sahifa yuklangach <h2> elementini kutamiz
        fio = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "h2.text-center.text-uppercase"))
        ).text.strip()


        # Fanlar (Top 6ta)
        fanlar_divs = wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div.card-header.card-div.text-center")
        ))
        fanlar = []
        for div in fanlar_divs[:6]:
            items = div.find_elements(By.TAG_NAME, "b")
            if len(items) >= 2:
                correct = items[0].text.strip()
                score = items[1].text.strip()
                fanlar.append((correct, score))
            else:
                fanlar.append(("?", "?"))

        # Ballar (Imtiyoz, Ijodiy, CEFR, Milliy, Umumiy)
        all_score_divs = wait.until(EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div.card-header.card-div.text-center")
        ))

        def extract_score_by_label(label: str):
            for div in all_score_divs:
                if label in div.text:
                    b = div.find_element(By.TAG_NAME, "b")
                    return b.text.strip()
            return "0"

        imtiyoz = extract_score_by_label("Imtiyoz ball")
        ijodiy = extract_score_by_label("Ijodiy ball")
        cefr = extract_score_by_label("CEFR ball")
        milliy = extract_score_by_label("Milliy sertifikat")
        umumiy = extract_score_by_label("Umumiy ball")

        # Vaqt
        vaqt = driver.find_element(By.TAG_NAME, "small").text.strip()

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
