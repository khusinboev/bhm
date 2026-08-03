"""'📊 Mandat saytdagi o'rni' bo'limi.

Oqim: tugma -> 7 xonali ID -> o'rin/ball/foiz + [📊 Batafsil] tugmasi.
Barcha sayt/kesh/statistika mantiqi src/utils/orin.py ichida.
"""

import logging

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import orin, rate_limit
from src.utils.mandat_parser import MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

orin_router = Router()

ORIN_BTN = "📊 Mandat saytdagi o'rni"

BUSY_TEXT = ("🚨 Hozir so'rovlar juda ko'p, navbat to'la.\n"
             "Iltimos, 1-2 daqiqadan so'ng qayta urinib ko'ring.")
DOWN_TEXT = ("🚨 mandat.uzbmb.uz sayti hozir javob bermayapti.\n"
             "Iltimos, birozdan so'ng qayta urinib ko'ring.")


class OrinState(StatesGroup):
    kutish = State()


def _markup(abt_id: str, detailed: bool) -> InlineKeyboardMarkup:
    if detailed:
        toggle_btn = InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"or:main:{abt_id}")
    else:
        toggle_btn = InlineKeyboardButton(text="📊 Batafsil", callback_data=f"or:det:{abt_id}")

    link_btn = InlineKeyboardButton(
        text="➡️ Ballingiz yetadigan yo'nalishlarni aniqlang",
        url="http://t.me/mandatjavobbot?start=ad",
    )

    return InlineKeyboardMarkup(inline_keyboard=[[toggle_btn], [link_btn]])

async def _build(abt_id: str, detailed: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    """Xabar matni va tugmalari. Salbiy holatda tugmasiz matn qaytadi."""
    res = await orin.get_rank(abt_id)
    if "info" not in res:
        return res["text"], None

    info = res["info"]
    stats = None
    try:
        stats = await orin.get_stats(info["s4subject"], info["s5subject"],
                                     info["ed_lang_id"])
    except (MandatBusy, MandatUnavailable):
        pass  # o'rin baribir ko'rsatiladi, statistikasiz
    except Exception:
        logging.exception("O'rin statistikasini olishda xatolik")

    if detailed:
        return orin.format_details(info, stats), _markup(abt_id, True)
    return (orin.format_main(info, stats, stale=res.get("stale", False)),
            _markup(abt_id, False))


@orin_router.message(F.text == ORIN_BTN, F.chat.type == ChatType.PRIVATE)
async def orin_btn(message: Message, state: FSMContext):
    await state.set_state(OrinState.kutish)
    await message.answer(
        "📊 <b>Mandat saytdagi o'rningiz</b>\n\n"
        "Fan majmuangiz bo'yicha reytingda nechanchi o'rinda ekaningizni bilish uchun "
        "7 xonali ID raqamingizni yuboring:",
        parse_mode="HTML",
        reply_markup=await UserPanels.to_back(),
    )


@orin_router.message(OrinState.kutish, F.text.regexp(r"^\d{7}$"), F.chat.type == ChatType.PRIVATE)
async def handle_orin_id(msg: Message):
    user_id = msg.from_user.id
    if not rate_limit.allow(user_id):
        await msg.answer("⏳ Juda tez-tez so'rov yubordingiz. "
                         "Iltimos, bir necha soniya kutib qayta urining.")
        return
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await msg.answer("❗ Iltimos, quyidagi kanallarga a'zo bo'ling:",
                         reply_markup=await CheckData.channels_btn(channels))
        return

    abt_id = msg.text.strip()
    loading = await msg.answer("🔍 Reytingdagi o'rningiz aniqlanmoqda, iltimos kuting...")
    text, markup = None, None
    try:
        text, markup = await _build(abt_id, detailed=False)
    except MandatBusy:
        text = BUSY_TEXT
    except MandatUnavailable:
        text = DOWN_TEXT
    except Exception:
        logging.exception(f"O'rin aniqlashda ichki xatolik (ID={abt_id})")
        text = "🚨 Ichki xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."

    try:
        await loading.delete()
    except: pass
    await answer_safe(msg, text, parse_mode="HTML", reply_markup=markup)


@orin_router.callback_query(F.data.startswith("or:"))
async def orin_switch(call: CallbackQuery):
    try:
        _, mode, abt_id = call.data.split(":")
    except ValueError:
        await call.answer()
        return

    try:
        text, markup = await _build(abt_id, detailed=(mode == "det"))
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        await call.answer()
    except (MandatBusy, MandatUnavailable):
        await call.answer("Sayt band — birozdan so'ng urinib ko'ring", show_alert=True)
    except Exception as e:
        # "message is not modified" kabi mayda xatolar — e'tiborsiz
        logging.debug(f"O'rin ko'rinishini almashtirishda xato: {e}")
        try:
            await call.answer()
        except: pass


@orin_router.message(OrinState.kutish, F.chat.type == ChatType.PRIVATE)
async def invalid_orin_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
