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
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

user_router = Router()

class MainState(StatesGroup):
    natija2 = State()


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





@user_router.message(F.text == "📝 Natijaga buyurtma berish", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Natijangizni buyurtma qilish uchun '<b>Abituriyent ruxsatnomasi</b>'ni <b>PDF</b> faylini yuboring", reply_markup=await UserPanels.main(),  parse_mode="html")
    await state.set_state(MainState.natija2)

@user_router.message(MainState.natija2, F.text == "📁 Mening buyurtmalarim", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:", parse_mode='html',
                             reply_markup=await CheckData.channels_btn(channels))
        return

    # So'nggi 10ta buyurtma
    sql.execute("SELECT abt_id, abt_name, abt_seriya, abt_pinfl, abt_date FROM bhm WHERE user_id = %s ORDER BY id DESC LIMIT 10", (user_id,))
    records = sql.fetchall()

    if not records:
        await message.answer("❗ Sizda hali hech qanday buyurtma mavjud emas.\n\nNatijangizni buyurtma qilish uchun '<b>Abituriyent ruxsatnomasi</b>'ni <b>PDF</b> faylini yuboring", parse_mode="html")
        return

    chunks = []
    current_chunk = "<b>👇 Sizning so‘nggi 10 ta buyurtmangiz:</b>\n\n"

    for row in records:
        abt_id, name, seriya, pinfl, date = row
        order_text = (
            f"<b>🆔 Abituriyent ID:</b> {abt_id}\n"
            f"<b>🗞 FIO:</b> {name}\n"
            f"<b>🪪 ID seriya/raqam:</b> {seriya}\n"
            f"<b>🔢 PINFL:</b> {pinfl}\n"
            f"<b>📆 Tug'ulgan sana:</b> {date.strftime('%d.%m.%Y')}\n\n"
        )

        # Har bir 3500–3700 belgidan keyin yangi xabarga o‘tamiz
        if len(current_chunk) + len(order_text) > 3500:
            chunks.append(current_chunk)
            current_chunk = ""

        current_chunk += order_text

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await message.answer(chunk, parse_mode="html")

@user_router.message(MainState.natija2, F.document, F.chat.type == ChatType.PRIVATE)
async def handle_pdf(message: Message):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
                             reply_markup=await CheckData.channels_btn(channels))
        return

    if not message.document.file_name.endswith(".pdf"):
        await message.answer(
            "✋ <b>Rasm(screenshot) emas PDF fayl jo'natishingizni so'raymiz</b>\n\n"
            "<i>Rasm formatidagi fayllardan matnni o'qib olishdagi noaniqliklar sabab BOT faqat PDF faylini qo'llab quvvatlaydi. Iltimos qayd varaqangizning haqiqiy PDF shaklidagi faylini jo'nating❕</i>",
            parse_mode="html"
        )
        return

    file = await bot.get_file(message.document.file_id)
    file_path = file.file_path
    file_data = await bot.download_file(file_path)

    text = ""
    with fitz.open(stream=file_data.read(), filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()

    import re
    try:
        abt_id = re.search(r"ID:\s*(\d+)", text).group(1)
        abt_name = re.search(r"F\.I\.O\.\:\s*(.+)", text).group(1)
        abt_seriya = re.search(r"Pasport.+?\:\s*([A-Z]+\s*\d+)", text).group(1)
        abt_pinfl = re.search(r"JShShIR\:\s*(\d+)", text).group(1)
        abt_date = re.search(r"Tug‘ilgan sanasi\:\s*(\d{2}\.\d{2}\.\d{4})", text).group(1)
    except Exception as e:
        await message.answer("❗ PDF dan ma'lumotlarni o'qib bo'lmadi. Iltimos, asl ruxsatnoma PDF faylini yuboring.")
        return

    try:
        sql.execute("""
            INSERT INTO bhm (user_id, abt_id, abt_name, abt_seriya, abt_pinfl, abt_date)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, abt_id) DO NOTHING
            RETURNING id
        """, (user_id, abt_id, abt_name, abt_seriya, abt_pinfl, abt_date))
        inserted = sql.fetchone()
        db.commit()

        if inserted:
            order_number = inserted[0]
        else:
            # allaqachon mavjud bo'lsa
            sql.execute("SELECT id FROM bhm WHERE user_id = %s AND abt_id = %s", (user_id, abt_id))
            order_number = sql.fetchone()[0]

        text = (f"✅ <b>Tabriklaymiz!:</b> {abt_id} ID raqamli abituriyent natijasiga buyurtma qabul qilindi \n\n       "
                f"<b>📑 Buyurtma tartib raqami:</b> {order_number}\n\n       F.ISH: {abt_name}\n\n       "
                f"<i>Eslatma: Natijalar elon qilinishi bilan ushbu bot avtomatik ravishda natijangizni sizga yuboradi!</i>\n\n       "
                f"<b>✔️ Buyurtma @mandat_uzbmbbot tomonidan amalga oshirilmoqda.</b>")

        await message.answer(text, parse_mode="html")

    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {e}")

@user_router.message(MainState.natija2, F.photo, F.chat.type == ChatType.PRIVATE)
async def handle_photo_warning(message: Message):
    await message.answer(
        "✋ <b>Rasm(screenshot) emas PDF fayl jo'natishingizni so'raymiz</b>\n\n"
        "<i>Rasm formatidagi fayllardan matnni o'qib olishdagi noaniqliklar sabab BOT faqat PDF faylini qo'llab quvvatlaydi. Iltimos qayd varaqangizning haqiqiy PDF shaklidagi faylini jo'nating❕</i>",
        parse_mode="html", reply_markup=await UserPanels.main()
    )

@user_router.message( MainState.natija2, F.chat.type == ChatType.PRIVATE)
async def handle_photo_warning(message: Message):
    await message.answer(
        "✋ <b>Rasm(screenshot) emas PDF fayl jo'natishingizni so'raymiz</b>\n\n"
        "<i>Rasm formatidagi fayllardan matnni o'qib olishdagi noaniqliklar sabab BOT faqat PDF faylini qo'llab quvvatlaydi. Iltimos qayd varaqangizning haqiqiy PDF shaklidagi faylini jo'nating❕</i>",
        parse_mode="html", reply_markup=await UserPanels.main()
    )

