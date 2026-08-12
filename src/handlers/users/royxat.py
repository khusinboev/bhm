"""Ro'yxatdan o'tish — /start bosilganda viloyat va telefon raqamini so'raydi.

Ma'lumot accounts jadvaliga yoziladi. Bir marta bergan foydalanuvchidan
qayta so'ralmaydi — u to'g'ridan-to'g'ri bosh menyuni ko'radi.
"""

import logging
import re

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from src.db import database
from src.keyboards.buttons import UserPanels

royxat_router = Router()

VILOYATLAR = [
    "Qoraqalpog'iston", "Andijon", "Buxoro", "Farg'ona", "Jizzax",
    "Xorazm", "Namangan", "Navoiy", "Qashqadaryo", "Samarqand",
    "Sirdaryo", "Surxondaryo", "Toshkent shahri", "Toshkent viloyati",
]

WELCOME = ("👋 Assalomu alaykum!\n\n"
           "Botdan foydalanish uchun bir marta ro'yxatdan o'tasiz.\n\n"
           "📍 Qaysi viloyatdansiz? Quyidagidan tanlang:")
ASK_PHONE = ("📱 Endi telefon raqamingizni yuboring.\n\n"
             "Quyidagi tugmani bosing — raqamingiz avtomatik yuboriladi.")
DONE = "✅ Ro'yxatdan o'tdingiz! Endi botdan to'liq foydalanishingiz mumkin."

MENU_TEXT = "<b>📄 Natijani ko'rish uchun quyidagi tugmalardan birini tanlang:</b>"

_PHONE_RE = re.compile(r"^\+?\d{9,15}$")


class Royxat(StatesGroup):
    viloyat = State()
    telefon = State()


def _viloyat_kb() -> ReplyKeyboardMarkup:
    rows, row = [], []
    for v in VILOYATLAR:
        row.append(KeyboardButton(text=v))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True,
                               one_time_keyboard=True)


def _telefon_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamimni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)


async def profil_toliqmi(user_id: int) -> bool:
    """Viloyat va telefon ikkalasi ham berilganmi."""
    try:
        row = await database.fetchone(
            "SELECT viloyat, phone FROM public.accounts WHERE user_id = %s",
            (user_id,))
    except Exception:
        logging.exception("Profilni tekshirib bo'lmadi")
        return True  # baza bilan muammo bo'lsa foydalanuvchini to'sib qo'ymaymiz
    return bool(row and row[0] and row[1])


async def _saqla(user_id: int, **kwargs) -> None:
    for key, value in kwargs.items():
        await database.execute(
            f"UPDATE public.accounts SET {key} = %s WHERE user_id = %s",
            (value, user_id))


async def show_menu(message: Message) -> None:
    await message.answer(MENU_TEXT, parse_mode="html",
                         reply_markup=await UserPanels.main2())


@royxat_router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_cmd(message: Message, state: FSMContext):
    try:
        await state.clear()
    except: pass

    if await profil_toliqmi(message.from_user.id):
        await show_menu(message)
        return

    await state.set_state(Royxat.viloyat)
    await message.answer(WELCOME, reply_markup=_viloyat_kb())


@royxat_router.message(Royxat.viloyat, F.text.in_(VILOYATLAR),
                       F.chat.type == ChatType.PRIVATE)
async def got_viloyat(message: Message, state: FSMContext):
    try:
        await _saqla(message.from_user.id, viloyat=message.text.strip())
    except Exception:
        logging.exception("Viloyatni saqlab bo'lmadi")
    await state.set_state(Royxat.telefon)
    await message.answer(ASK_PHONE, reply_markup=_telefon_kb())


@royxat_router.message(Royxat.viloyat, F.chat.type == ChatType.PRIVATE)
async def wrong_viloyat(message: Message):
    await message.answer("✋ Iltimos, viloyatingizni quyidagi tugmalardan tanlang:",
                         reply_markup=_viloyat_kb())


@royxat_router.message(Royxat.telefon, F.contact, F.chat.type == ChatType.PRIVATE)
async def got_contact(message: Message, state: FSMContext):
    # Boshqa odamning kontaktini yuborishning oldini olamiz
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("✋ Iltimos, o'z raqamingizni yuboring.",
                             reply_markup=_telefon_kb())
        return
    await _finish(message, state, message.contact.phone_number)


@royxat_router.message(Royxat.telefon, F.text.regexp(_PHONE_RE),
                       F.chat.type == ChatType.PRIVATE)
async def got_typed_phone(message: Message, state: FSMContext):
    await _finish(message, state, message.text.strip())


@royxat_router.message(Royxat.telefon, F.chat.type == ChatType.PRIVATE)
async def wrong_phone(message: Message):
    await message.answer(
        "✋ Iltimos, quyidagi tugma orqali raqamingizni yuboring "
        "(yoki +998XXXXXXXXX ko'rinishida yozing).",
        reply_markup=_telefon_kb())


async def _finish(message: Message, state: FSMContext, phone: str) -> None:
    try:
        await _saqla(message.from_user.id, phone=phone)
    except Exception:
        logging.exception("Telefon raqamini saqlab bo'lmadi")
    try:
        await state.clear()
    except: pass
    await message.answer(DONE)
    await show_menu(message)
