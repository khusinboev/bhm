import logging
import time
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

data_router = Router()


def get_abiturient_info_by_id(user_id: str) -> str:
    options = Options()
    # options.add_argument("--headless=new")  # "new" rejim Chrome 109+ uchun kerakli
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get("https://mandat.uzbmb.uz/")
        wait = WebDriverWait(driver, 15)

        # Formani to‘ldirish va qidirish
        input_field = wait.until(EC.presence_of_element_located((By.ID, "AbiturID")))
        search_btn = wait.until(EC.element_to_be_clickable((By.ID, "SearchBtn1")))

        input_field.clear()
        input_field.send_keys(str(user_id))
        time.sleep(1)
        search_btn.click()

        # Batafsil tugmasini bosish
        detail_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-info")))
        detail_button.click()

        # FIO topish
        fio = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h2.text-center.text-uppercase"))).text.strip()

        # Ball va to‘g‘ri javoblar
        card_headers = driver.find_elements(By.CSS_SELECTOR, "div.card-header.card-div.text-center")

        # Asosiy fanlar
        fanlar = []
        for i in range(3):  # 3ta asosiy fan
            texts = card_headers[i].text.split("\n")
            correct = texts[1].replace("To’g’ri javoblar soni:", "").strip()
            score = texts[3].replace("Ball:", "").strip()
            fanlar.append((correct, score))

        # Ixtisoslik fanlari
        for i in range(3, 6):
            texts = card_headers[i].text.split("\n")
            correct = texts[0].replace("To’g’ri javoblar soni:", "").strip()
            score = texts[2].replace("Ball:", "").strip()
            fanlar.append((correct, score))

        # Ballar
        imtiyoz = card_headers[6].find_element(By.TAG_NAME, "b").text.strip()
        ijodiy = card_headers[7].find_element(By.TAG_NAME, "b").text.strip()
        cefr = card_headers[8].find_element(By.TAG_NAME, "b").text.strip()
        milliy = card_headers[9].find_element(By.TAG_NAME, "b").text.strip()
        umumiy = card_headers[10].find_element(By.TAG_NAME, "b").text.strip()

        # Vaqt
        vaqt = driver.find_element(By.TAG_NAME, "small").text.strip()

        # Matn tayyorlash
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


# === HANDLER ===
@data_router.message(F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    abt_id = msg.text.strip()

    await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")

    try:
        text = get_abiturient_info_by_id(abt_id)
        if text.startswith("❌") or "Xatolik" in text:
            await msg.answer(f"🚫 <b>ID: {abt_id}</b> uchun ma'lumot topilmadi.", parse_mode="HTML")
        else:
            await msg.answer(text, parse_mode="HTML")
    except Exception as e:
        logging.exception("❌ Ichki xatolik:")
        await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko‘ring.")
