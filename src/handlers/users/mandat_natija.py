"""'📊 MANDAT NATIJASI' bo'limi — yakuniy natijani WebApp orqali ko'rish.

Havolani admin bot ichidan o'rnatadi:
    /adwep https://...      — havolani o'rnatish (yoki almashtirish)
    /adwep                  — joriy havolani ko'rish
    /adwep ochirish         — havolani olib tashlash

Havola bazada saqlanadi (sozlamalar jadvali) — bot qayta ishga tushsa ham
joyida qoladi va tugma avtomatik ishlay boshlaydi.
"""

import logging
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           WebAppInfo)

from config import ADMIN_ID
from src.utils import settings

mandat_natija_router = Router()

NATIJA_BTN = "📊 MANDAT NATIJASI"
WEBAPP_KEY = "mandat_webapp_url"

OPEN_BTN_TEXT = "📥 Mandat natijasini ko'rish"
INTRO_TEXT = "Yakuniy mandat natijasini ko'rish👇"


def _webapp_markup(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=OPEN_BTN_TEXT, web_app=WebAppInfo(url=url))
    ]])


def _valid_url(url: str) -> bool:
    """Telegram WebApp faqat to'g'ri HTTPS havolani qabul qiladi."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.netloc)


@mandat_natija_router.message(F.text == NATIJA_BTN, F.chat.type == ChatType.PRIVATE)
async def show_natija(message: Message, state: FSMContext):
    try:
        await state.clear()
    except: pass

    url = await settings.get(WEBAPP_KEY)
    if not url:
        if message.from_user.id in ADMIN_ID:
            await message.answer(
                "⚙️ WebApp havolasi hali o'rnatilmagan.\n\n"
                "O'rnatish uchun: <code>/adwep https://...</code>",
                parse_mode="HTML")
        else:
            await message.answer(
                "⏳ Yakuniy mandat natijalari hali e'lon qilinmagan.\n"
                "E'lon qilinishi bilan shu bo'limda ko'rishingiz mumkin bo'ladi.")
        return

    await message.answer(INTRO_TEXT, reply_markup=_webapp_markup(url))


@mandat_natija_router.message(Command("adwep"), F.from_user.id.in_(ADMIN_ID),
                              F.chat.type == ChatType.PRIVATE)
async def set_webapp(message: Message, command: CommandObject):
    arg = (command.args or "").strip()
    current = await settings.get(WEBAPP_KEY)

    if not arg:
        if current:
            await message.answer(
                f"🔗 Joriy WebApp havolasi:\n<code>{current}</code>\n\n"
                "Almashtirish: <code>/adwep https://...</code>\n"
                "Olib tashlash: <code>/adwep ochirish</code>",
                parse_mode="HTML", reply_markup=_webapp_markup(current))
        else:
            await message.answer(
                "🔗 WebApp havolasi o'rnatilmagan.\n\n"
                "O'rnatish: <code>/adwep https://...</code>", parse_mode="HTML")
        return

    if arg.lower() in ("ochirish", "o'chirish", "delete", "off"):
        await settings.delete(WEBAPP_KEY)
        await message.answer("🗑 WebApp havolasi olib tashlandi. "
                             f"«{NATIJA_BTN}» bo'limi vaqtincha kutish xabarini ko'rsatadi.")
        return

    if not _valid_url(arg):
        await message.answer(
            "❌ Havola noto'g'ri. Telegram WebApp uchun havola <b>https://</b> "
            "bilan boshlanishi kerak.\n\nMasalan: <code>/adwep https://example.uz/natija</code>",
            parse_mode="HTML")
        return

    try:
        await settings.set_value(WEBAPP_KEY, arg)
    except Exception as e:
        logging.exception("WebApp havolasini saqlab bo'lmadi")
        await message.answer(f"🚨 Havolani saqlab bo'lmadi: {e}")
        return

    await message.answer(
        f"✅ WebApp havolasi o'rnatildi:\n<code>{arg}</code>\n\n"
        f"«{NATIJA_BTN}» tugmasi endi ishlaydi. Quyida sinab ko'ring:",
        parse_mode="HTML", reply_markup=_webapp_markup(arg))
