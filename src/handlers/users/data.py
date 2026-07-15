import logging

import redis.asyncio as aioredis
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils.mandat_parser import fetch_details, format_full_report, MandatBusy, MandatUnavailable

data_router = Router()


class MainState2(StatesGroup):
    natija = State()


# Kesh sozlamalari. Redis ishlamay qolsa ham bot ishlashda davom etadi —
# shunchaki keshsiz, har safar saytdan oladi.
# Natijalar e'lon qilingach o'zgarmaydi — uzun kesh saytga yukni kamaytiradi
CACHE_TIMEOUT = 3600  # 1 soat
CACHE_PREFIX = "mandat:full:"
redis = aioredis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


async def get_from_cache(abt_id: str) -> str | None:
    try:
        return await redis.get(CACHE_PREFIX + abt_id)
    except Exception as e:
        logging.warning(f"Redis'dan o'qib bo'lmadi: {e}")
        return None


async def save_to_cache(abt_id: str, data: str) -> None:
    try:
        await redis.set(CACHE_PREFIX + abt_id, data, ex=CACHE_TIMEOUT)
    except Exception as e:
        logging.warning(f"Redis'ga yozib bo'lmadi: {e}")


async def get_abiturient_info(abt_id: str) -> str:
    cached = await get_from_cache(abt_id)
    if cached:
        return cached

    try:
        info = await fetch_details(abt_id)
    except MandatBusy:
        return "🚨 Hozir so'rovlar juda ko'p, navbat to'la.\nIltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring."
    except MandatUnavailable:
        return "🚨 mandat.uzbmb.uz sayti hozir javob bermayapti.\nIltimos, birozdan so'ng qayta urinib ko'ring."

    if info is None:
        return "❌ Bunday ID topilmadi. Iltimos, ID raqamini tekshiring."

    result = format_full_report(info)
    await save_to_cache(abt_id, result)
    return result


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
    await msg.answer(result, parse_mode="HTML")


@data_router.message(MainState2.natija, F.chat.type == ChatType.PRIVATE)
async def invalid_natija_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
