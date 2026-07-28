"""'🎯 Balingizga mos yo'nalish' bo'limi.

Tuzilishi:
  🎯 tugmasi -> ichki menyu: [🤖 Botda ko'rish] [🌐 Saytda ko'rish (WebApp)]
  🤖 Botda ko'rish -> 7 xonali ID -> natija sahifalab (inline ⬅️ 1/53 ➡️)
Sahifa almashtirishda sayt so'ralmaydi — ma'lumot Postgres snapshot'dan
olinadi (barcha sayt/kesh mantiqi src/utils/ballinfo.py ichida).
"""

import logging

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo)

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import ballinfo, rate_limit
from src.utils.mandat_parser import MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

yonalish_router = Router()

WEBAPP_URL = "https://mandat.uzbmb.uz/Bakalavr/BallInfoByResult"

BOT_VIEW_BTN = "🤖 Botda ko'rish"


class YonalishState(StatesGroup):
    kutish = State()


def _submenu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BOT_VIEW_BTN)],
            [KeyboardButton(text="🌐 Saytda ko'rish", web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text="🔙 Ortga")],
        ],
        resize_keyboard=True,
    )


def _page_markup(abt_id: str, page: int, total: int) -> InlineKeyboardMarkup | None:
    if total <= 1:
        return None
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="⬅️", callback_data=f"bi:{abt_id}:{page - 1}"))
    row.append(InlineKeyboardButton(text=f"📄 {page}/{total}", callback_data="bi:noop"))
    if page < total:
        row.append(InlineKeyboardButton(text="➡️", callback_data=f"bi:{abt_id}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


@yonalish_router.message(F.text == "🎯 Balingizga mos yo'nalish", F.chat.type == ChatType.PRIVATE)
async def yonalish_btn(message: Message, state: FSMContext):
    try:
        await state.clear()
    except: pass
    await message.answer(
        "🎯 <b>Balingizga mos yo'nalishlar</b>\n\n"
        "Balingiz bilan qaysi yo'nalishlarga kira olishingizni ko'rish usulini tanlang:",
        parse_mode="HTML",
        reply_markup=_submenu_kb(),
    )


@yonalish_router.message(F.text == BOT_VIEW_BTN, F.chat.type == ChatType.PRIVATE)
async def yonalish_bot_view(message: Message, state: FSMContext):
    await state.set_state(YonalishState.kutish)
    await message.answer(
        "📝 7 xonali ID raqamingizni yuboring:",
        reply_markup=await UserPanels.to_back(),
    )


@yonalish_router.message(YonalishState.kutish, F.text.regexp(r"^\d{7}$"), F.chat.type == ChatType.PRIVATE)
async def handle_yonalish_id(msg: Message):
    user_id = msg.from_user.id
    if not rate_limit.allow(user_id):
        await msg.answer("⏳ Juda tez-tez so'rov yubordingiz. Iltimos, bir necha soniya kutib qayta urining.")
        return
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()
    loading_msg = await msg.answer("🔍 Yo'nalishlar aniqlanmoqda, iltimos kuting...")
    text, markup = None, None
    try:
        res = await ballinfo.get_data(abt_id)
        if "data" in res:
            text, total = ballinfo.format_page(abt_id, res["data"], page=1,
                                               stale=res.get("stale", False))
            markup = _page_markup(abt_id, 1, total)
        else:
            text = res["text"]
    except MandatBusy:
        text = "🚨 Hozir so'rovlar juda ko'p, navbat to'la.\nIltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
    except MandatUnavailable:
        text = "🚨 mandat.uzbmb.uz sayti hozir javob bermayapti.\nIltimos, birozdan so'ng qayta urinib ko'ring."
    except Exception:
        logging.exception(f"Yo'nalishlarni olishda ichki xatolik (ID={abt_id})")
        text = "🚨 Ichki xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."

    try:
        await loading_msg.delete()
    except: pass
    await answer_safe(msg, text, parse_mode="HTML", reply_markup=markup)


@yonalish_router.callback_query(F.data.startswith("bi:"))
async def yonalish_page(call: CallbackQuery):
    if call.data == "bi:noop":
        await call.answer()
        return
    try:
        _, abt_id, page_s = call.data.split(":")
        page = int(page_s)
    except ValueError:
        await call.answer()
        return

    try:
        # Sahifa almashtirish snapshot'dan — saytga bormaydi (6 soat ichida)
        res = await ballinfo.get_data(abt_id)
        if "data" not in res:
            await call.answer("Ma'lumot eskirgan — ID'ni qaytadan yuboring", show_alert=True)
            return
        text, total = ballinfo.format_page(abt_id, res["data"], page=page,
                                           stale=res.get("stale", False))
        await call.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=_page_markup(abt_id, min(page, total), total))
        await call.answer()
    except (MandatBusy, MandatUnavailable):
        await call.answer("Sayt band — birozdan so'ng urinib ko'ring", show_alert=True)
    except Exception as e:
        # "message is not modified" kabi mayda xatolar — shunchaki e'tiborsiz
        logging.debug(f"Sahifa almashtirishda xato: {e}")
        try:
            await call.answer()
        except: pass


@yonalish_router.message(YonalishState.kutish, F.chat.type == ChatType.PRIVATE)
async def invalid_yonalish_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
