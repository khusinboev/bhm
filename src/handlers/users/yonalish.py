"""'🎯 Balingizga mos yo'nalish' bo'limi.

Natija bo'limiga o'xshash oqim: tugma -> 7 xonali ID -> javob.
Barcha sayt/kesh/saqlash mantiqi src/utils/ballinfo.py ichida.
"""

import logging

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import ballinfo, rate_limit
from src.utils.mandat_parser import MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

yonalish_router = Router()


class YonalishState(StatesGroup):
    kutish = State()


@yonalish_router.message(F.text == "🎯 Balingizga mos yo'nalish", F.chat.type == ChatType.PRIVATE)
async def yonalish_btn(message: Message, state: FSMContext):
    await state.set_state(YonalishState.kutish)
    await message.answer(
        "🎯 Balingiz bilan qaysi yo'nalishlarga kira olishingizni bilish uchun "
        "7 xonali ID raqamingizni yuboring:",
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
    try:
        result = await ballinfo.get_report(abt_id)
    except MandatBusy:
        result = "🚨 Hozir so'rovlar juda ko'p, navbat to'la.\nIltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
    except MandatUnavailable:
        result = "🚨 mandat.uzbmb.uz sayti hozir javob bermayapti.\nIltimos, birozdan so'ng qayta urinib ko'ring."
    except Exception:
        logging.exception(f"Yo'nalishlarni olishda ichki xatolik (ID={abt_id})")
        result = "🚨 Ichki xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."

    try:
        await loading_msg.delete()
    except: pass
    await answer_safe(msg, result, parse_mode="HTML")


@yonalish_router.message(YonalishState.kutish, F.chat.type == ChatType.PRIVATE)
async def invalid_yonalish_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
