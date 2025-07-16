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
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
    print("1-bosqich")
    driver = webdriver.Chrome(options=options)
    print("2-bosqich")
    try:
        driver.get("https://mandat.uzbmb.uz/")
        print("3-bosqich")
        wait = WebDriverWait(driver, 30)
        print("4-bosqich")
        # Sahifa to‘liq yuklanganini kutish
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        print("5-bosqich")
        # === Screenshot olish ===
        timestamp = int(time.time())
        screenshot_path = f"screenshot_after_ready_{timestamp}.png"
        driver.save_screenshot(screenshot_path)
        print(f"✅ Screenshot saqlandi: {screenshot_path}")

        # Kirish qutisi va tugmani kutish
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        search_btn_elem = wait.until(EC.presence_of_element_located((By.ID, "SearchBtn1")))
        wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))
        print("6-bosqich")
        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1.5)
        search_btn_elem.click()
        print("7-bosqich")
        # "Batafsil" tugmasi
        detail_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_button.click()
        print("8-bosqich")
        # FIO
        fio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h2.text-center.text-uppercase"))).text.strip()
        print("9-bosqich")
        # Ballar va fanlar
        card_headers = driver.find_elements(By.CSS_SELECTOR, "div.card-header.card-div.text-center")
        fanlar = []

        for i in range(3):
            texts = card_headers[i].text.split("\n")
            correct = texts[1].replace("To’g’ri javoblar soni:", "").strip()
            score = texts[3].replace("Ball:", "").strip()
            fanlar.append((correct, score))

        for i in range(3, 6):
            texts = card_headers[i].text.split("\n")
            correct = texts[0].replace("To’g’ri javoblar soni:", "").strip()
            score = texts[2].replace("Ball:", "").strip()
            fanlar.append((correct, score))

        imtiyoz = card_headers[6].find_element(By.TAG_NAME, "b").text.strip()
        ijodiy = card_headers[7].find_element(By.TAG_NAME, "b").text.strip()
        cefr = card_headers[8].find_element(By.TAG_NAME, "b").text.strip()
        milliy = card_headers[9].find_element(By.TAG_NAME, "b").text.strip()
        umumiy = card_headers[10].find_element(By.TAG_NAME, "b").text.strip()

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
