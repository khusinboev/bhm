import logging

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import rate_limit, result_service
from src.utils.mandat_parser import format_full_report, MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

data_router = Router()


class MainState2(StatesGroup):
    natija = State()


async def get_abiturient_info(abt_id: str) -> str:
    # Barcha kesh/baza mantiqi result_service ichida:
    # yakuniy natija — doim bazadan, "hali chiqmagan" — 3 daqiqalik kesh
    try:
        info = await result_service.get_result(abt_id)
    except MandatBusy:
        return "🚨 Hozir so'rovlar juda ko'p, navbat to'la.\nIltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
    except MandatUnavailable:
        return "🚨 mandat.uzbmb.uz sayti hozir javob bermayapti.\nIltimos, birozdan so'ng qayta urinib ko'ring."

    if info is None:
        return "❌ Bunday ID topilmadi. Iltimos, ID raqamini tekshiring.\n\n<i>Mandat saytidagi uzilishlar sababli ham sizning natijangiz chiqmayotgan bo'lishi mumkin. Birozdan so'ng qayta urinib ko'ring!</i>"

    return format_full_report(info)


@data_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def natija_btn(message: Message, state: FSMContext):
    # Avval state o'rnatiladi — yo'riqnoma xabari yuborilmasa ham foydalanuvchi ID yubora oladi
    await state.set_state(MainState2.natija)
    try:
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id="@Second_Polat",
            message_id=733,
            reply_markup=await UserPanels.to_back(),
        )
    except Exception as e:
        logging.warning(f"Yo'riqnoma xabarini nusxalab bo'lmadi: {e}")
        await message.answer(
            "📊 Natijangizni ko'rish uchun 7 xonali ID raqamingizni yuboring:",
            reply_markup=await UserPanels.to_back(),
        )


@data_router.message(MainState2.natija, F.text.regexp(r"^\d{7}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id_query(msg: Message):
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
    loading_msg = await msg.answer("🔍 Ma'lumotlar olinmoqda, iltimos kuting...")
    try:
        result = await get_abiturient_info(abt_id)
    except Exception:
        logging.exception(f"Natija olishda ichki xatolik (ID={abt_id})")
        result = "🚨 Ichki xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."

    try:
        await loading_msg.delete()
    except: pass
    await answer_safe(msg, result, parse_mode="HTML")


@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def invalid_natija_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
