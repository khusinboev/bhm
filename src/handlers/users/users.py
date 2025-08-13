import asyncio
import os
import re
from concurrent.futures.thread import ThreadPoolExecutor
import time
import logging
import fitz
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

user_router = Router()

class MainState(StatesGroup):
    natija2 = State()

playwright_instance = None
browser_instance = None
semaphore = asyncio.Semaphore(5)  # bir vaqtning o'zida 5 ta so‘rov

@user_router.message(CommandStart())
async def start_cmd1(message: Message):
    await message.answer("<b>Botimizdan foydalanish uchun quyidagi tugmalardan birini tanlang</b>",
                         reply_markup=await UserPanels.main2(),  parse_mode="html")

@user_router.callback_query(F.data == "check", F.message.chat.type == ChatType.PRIVATE)
async def check(call: CallbackQuery):
    user_id = call.from_user.id
    try:
        check_status, channels = await CheckData.check_member(bot, user_id)
        if check_status:
            await bot.send_message(chat_id=user_id,
                                   text="<b>Botimizdan foydalanish uchun quyidagi tugmalardan birini tanlang</b>",
                                   reply_markup=await UserPanels.main2(),
                                   parse_mode="html")
            try:
                await call.message.delete()
                await call.answer()
            except:
                pass
        else:
            try:
                await call.answer(show_alert=True, text="Botimizdan foydalanish uchun barcha kanallarga a'zo bo'ling")
            except: pass
    except Exception as e:
        await bot.forward_message(chat_id=ADMIN_ID[0], from_chat_id=call.message.chat.id, message_id=call.message.message_id)
        await bot.send_message(chat_id=ADMIN_ID[0], text=f"Error in check:\n{e}")



@user_router.message(MainState.natija2, F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())

@user_router.message(F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    try:
        await state.clear()
    except: pass





async def parse_mandat(abt_id: str):
    global playwright_instance, browser_instance
    if browser_instance is None:
        playwright_instance = await async_playwright().start()
        browser_instance = await playwright_instance.chromium.launch(headless=True)

    context = await browser_instance.new_context(
        viewport={'width': 1280, 'height': 720},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
    )

    try:
        page = await context.new_page()
        await page.goto("https://mandat.uzbmb.uz/", timeout=20000)
        await page.fill('#AbiturID', abt_id)
        await page.click('#SearchBtn1')
        await page.wait_for_selector('table.table tbody tr', timeout=15000)

        # jadvaldan kerakli satrni topish
        rows = await page.query_selector_all('table.table tbody tr')
        matched_href = None
        matched_index = None
        for idx, row in enumerate(rows, start=1):
            cells = await row.query_selector_all('td')
            if cells:
                if (await cells[0].inner_text()).strip() == abt_id:
                    link = await row.query_selector('a.btn.btn-info')
                    if link:
                        matched_href = await link.get_attribute('href')
                        matched_index = idx
                    break

        if not matched_href:
            return None

        # sahifa raqami
        page_number = 1
        try:
            page_form = await page.query_selector('li.page-item.active form')
            if page_form:
                inp = await page_form.query_selector('input[name="pageNumber"]')
                if inp:
                    val = await inp.get_attribute('value')
                    if val and val.isdigit():
                        page_number = int(val)
        except:
            pass

        umumiy_orn = (page_number - 1) * 10 + matched_index

        # batafsil ma'lumot sahifasiga o'tish
        await page.goto(f"https://mandat.uzbmb.uz{matched_href}", timeout=15000)
        fio = await page.eval_on_selector('xpath=//div[contains(text(),"F.I.SH")]/b', 'el => el.textContent')
        fio = fio.strip()

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        umumiy_ball = "?"
        umumiy_div = soup.select_one("div.bg-success.text-white b")
        if umumiy_div:
            umumiy_ball = umumiy_div.text.strip()

        return {
            "fio": fio,
            "abt_id": abt_id,
            "umumiy_ball": umumiy_ball,
            "orn": umumiy_orn
        }
    except Exception as e:
        logging.exception(f"Parsingda xatolik: {e}")
        return None
    finally:
        await context.close()


@user_router.message(F.text == "📝 Mandat natijasiga buyurtma berish", F.chat.type == ChatType.PRIVATE)
async def ask_id(message: Message, state: FSMContext):
    await message.answer("📝 Iltimos, ID raqamingizni yuboring:", reply_markup=await UserPanels.main())
    await state.set_state(MainState.natija2)


@user_router.message(F.text == "📁 Mening buyurtmalarim", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer(
            "❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
            parse_mode='html',
            reply_markup=await CheckData.channels_btn(channels)
        )
        return

    sql.execute("""TRUNCATE TABLE bhm RESTART IDENTITY;""")
    db.commit()
    # So‘nggi 6 ta buyurtma
    sql.execute("""
        SELECT abt_id, abt_name, umumiy_ball, umumiy_orn, id
        FROM bhm
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 6
    """, (user_id,))
    records = sql.fetchall()

    if not records:
        await message.answer(
            "❗ Sizda hali hech qanday buyurtma mavjud emas.\n\n"
            "Natijangizni buyurtma qilish uchun '<b>ID raqamingiz</b>'ni yuboring.",
            parse_mode="html"
        )
        return

    total_orders = len(records)
    text_header = f"<b>👇 Sizning so‘nggi {total_orders} ta buyurtmangiz:</b>\n\n"

    body = ""
    for abt_id, fio, umumiy_ball, umumiy_orn, order_num in records:
        body += (
            f"✅ <b>{abt_id}</b> ID raqamli abituriyent ruxsatnomasiga buyurtma qabul qilindi\n"
            f"📑 Buyurtma tartib raqami: {int(order_num)+100}\n"
            f"F.I.SH: {fio}\n"
            f"Umumiy ball: {umumiy_ball}\n"
            f"Mandat saytidagi o‘rningiz: {umumiy_orn}\n\n"
        )

    await message.answer(text_header + body, parse_mode="html")


@user_router.message(MainState.natija2, F.text.regexp(r"^\d{6,8}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id(message: Message, state: FSMContext):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer(
            "❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
            reply_markup=await CheckData.channels_btn(channels)
        )
        return

    abt_id = message.text.strip()
    # Avval bazadan qidiramiz
    sql.execute("""
        SELECT abt_id, abt_name, umumiy_ball, umumiy_orn, id
        FROM bhm
        WHERE user_id = %s AND abt_id = %s
    """, (user_id, abt_id))
    record = sql.fetchone()

    if record:
        # Agar bazada mavjud bo‘lsa
        abt_id, fio, umumiy_ball, umumiy_orn, order_number = record
    else:
        # Agar yo‘q bo‘lsa sayt orqali olish
        loading = await message.answer("🔍 Ma'lumotlar olinmoqda, kuting...")
        async with semaphore:
            data = await parse_mandat(abt_id)
        await loading.delete()

        if not data:
            await message.answer("❌ ID topilmadi yoki ma'lumot olishda xatolik yuz berdi.")
            return

        fio = data["fio"]
        umumiy_ball = data["umumiy_ball"].replace(",", ".")  # <-- shu qo‘shiladi
        umumiy_orn = data["orn"]

        # Bazaga yozish (abt_id unique bo‘lishi uchun constraint qo‘yilgan bo‘lishi kerak)
        sql.execute("""
            INSERT INTO bhm (user_id, abt_id, abt_name, umumiy_ball, umumiy_orn, abt_seriya, abt_pinfl, abt_date)
            VALUES (%s, %s, %s, %s, %s, '', '', NOW())
            ON CONFLICT (user_id, abt_id) DO NOTHING
            RETURNING id
        """, (user_id, abt_id, fio, umumiy_ball, umumiy_orn))
        inserted = sql.fetchone()
        db.commit()

        if inserted:
            order_number = inserted[0]
        else:
            sql.execute("SELECT id FROM bhm WHERE abt_id = %s", (abt_id,))
            order_number = sql.fetchone()[0]

    # Foydalanuvchiga javob
    text = (
        f"<b>✅ Tabriklaymiz:</b> {abt_id} ID raqamli abituriyent ruxsatnomasiga buyurtma qabul qilindi\n\n"
        f"<b>📑 Buyurtma tartib raqami:</b> {int(order_number)+100}\n"
        f"🪪 F.I.SH: {fio}\n"
        f"🎓 Umumiy ball: {umumiy_ball}\n"
        f"📊 Mandat saytidagi o‘rningiz: {umumiy_orn}\n\n"
        f"<i><b>Eslatma:</b> YAKUNIY MANDAT NATIJALARI e'lon qilinishi bilan ushbu bot avtomatik ravishda natijangizni yuboradi!</i>\n\n"
        f"<b>✔️ Buyurtma @mandat_uzbmbbot tomonidan amalga oshirilmoqda.</b>"
    )

    await message.answer(text, parse_mode="html")


@user_router.message(MainState.natija2, F.chat.type == ChatType.PRIVATE)
async def invalid_input(message: Message):
    await message.answer("✋ Iltimos, faqat ID raqamini kiriting (faqat raqamlar).", reply_markup=await UserPanels.main())
