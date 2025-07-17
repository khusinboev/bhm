import logging
import time
import asyncio
from datetime import datetime
import redis.asyncio as aioredis
from aiogram import Router, F
from aiogram.types import Message
from aiogram.enums import ChatType
from bs4 import BeautifulSoup
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from playwright.async_api import async_playwright
from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

data_router = Router()

# Global variables for Playwright
playwright = None
browser = None

# Semaphore to limit concurrent requests
semaphore = asyncio.Semaphore(10)


# FSM
class MainState2(StatesGroup):
    natija = State()


# Cache settings
CACHE_TIMEOUT = 300  # 5 minutes
redis = aioredis.Redis(host="localhost", port=6379, decode_responses=True)


async def get_from_cache(abt_id: str) -> str | None:
    return await redis.get(abt_id)


async def save_to_cache(abt_id: str, data: str):
    await redis.set(abt_id, data, ex=CACHE_TIMEOUT)


# Asynchronous parsing function using Playwright
async def async_parse_with_playwright(abt_id: str) -> str:
    global playwright, browser
    if browser is None:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)

    context = await browser.new_context(
        viewport={'width': 1280, 'height': 720},
        user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
    )
    try:
        page = await context.new_page()
        await page.goto("https://mandat.uzbmb.uz/", timeout=20000)
        await page.wait_for_selector('#AbiturID', timeout=15000)
        await page.fill('#AbiturID', abt_id)
        await page.click('#SearchBtn1')
        await page.wait_for_selector('a.btn.btn-info', timeout=15000)
        await page.click('a.btn.btn-info')
        await page.wait_for_selector('xpath=//div[contains(text(),"F.I.SH")]/b', timeout=15000)
        fio = await page.eval_on_selector('xpath=//div[contains(text(),"F.I.SH")]/b', 'el => el.textContent')
        fio = fio.strip()
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        card_headers = soup.select("div.card-header.card-div.text-center")
        fanlar = []
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
        umumiy_ball = "?"
        umumiy_div = soup.find("div", class_="card-header card-div text-center",
                               string=lambda t: t and "Umumiy ball" in t)
        if not umumiy_div:
            umumiy_div = soup.find("div", class_="bg-success")
        if umumiy_div:
            umumiy_b = umumiy_div.find("b")
            if umumiy_b:
                umumiy_ball = umumiy_b.text.strip()
        vaqt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        matn = f"""<b>BAKALAVR 2025</b>
_______
<b>FIO</b>:  {fio}
🆔:  <b>{abt_id}</b>
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
        return matn
    except Exception as e:
        logging.exception(f"❌ Xatolik ID {abt_id} uchun:")
        return "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."
    finally:
        await context.close()


# Asynchronous function to get abiturient info
async def get_abiturient_info_async(abt_id: str) -> str:
    cached = await get_from_cache(abt_id)
    if cached:
        return cached
    if semaphore._value <= 1:
        return "🚨 Hozirda juda ko‘p so‘rovlar bo‘layapti.\nIltimos, 30 soniyadan keyin qayta urinib ko‘ring."
    async with semaphore:
        result = await async_parse_with_playwright(abt_id)
    if not result.startswith("❌"):
        await save_to_cache(abt_id, result)
    return result


# Handler functions
@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id="@Second_Polat",
        message_id=733,
        reply_markup=await UserPanels.to_back(),
    )
    await state.set_state(MainState2.natija)


@data_router.message(MainState2.natija, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
    user_id = msg.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return
    abt_id = msg.text.strip()
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")
    try:
        result = await get_abiturient_info_async(abt_id)
        await loading_msg.delete()
        await msg.answer(result, parse_mode="HTML")
    except Exception as e:
        logging.exception("❌ Ichki xatolik:")
        await loading_msg.delete()
        await msg.answer("❌ Ichki xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.")