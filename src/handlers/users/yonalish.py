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
                           KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo,
                           BufferedInputFile)

from config import bot
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import ballinfo, rate_limit
from src.utils.mandat_parser import MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

yonalish_router = Router()

WEBAPP_URL = "https://mandat.uzbmb.uz/Bakalavr/BallInfoByResult"

BOT_VIEW_BTN = "🤖 Botda ko'rish"

FILTER_PAGE_SIZE = 8
KIND_TO_CODE = {"region": "r", "university": "u", "faculty": "f"}
CODE_TO_KIND = {v: k for k, v in KIND_TO_CODE.items()}
KIND_LABEL = {"region": "🌍 Hudud", "university": "🏛 OTM", "faculty": "🎓 Yo'nalish"}


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


def _page_markup(abt_id: str, page: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    if total > 1:
        row = []
        if page > 1:
            row.append(InlineKeyboardButton(text="⬅️", callback_data=f"bi:page:{abt_id}:{page - 1}"))
        row.append(InlineKeyboardButton(text=f"📄 {page}/{total}", callback_data="bi:noop"))
        if page < total:
            row.append(InlineKeyboardButton(text="➡️", callback_data=f"bi:page:{abt_id}:{page + 1}"))
        rows.append(row)

    # Talab bo'yicha filtr tugmasi pagination qatori ostida joylashadi.
    rows.append([
        InlineKeyboardButton(text="🔎 Filtrlar", callback_data=f"bi:menu:{abt_id}"),
        InlineKeyboardButton(text="🧾 PDF", callback_data=f"bi:pdf:{abt_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _filter_menu_markup(abt_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=KIND_LABEL["region"], callback_data=f"bi:pick:{abt_id}:r:1")],
        [InlineKeyboardButton(text=KIND_LABEL["university"], callback_data=f"bi:pick:{abt_id}:u:1")],
        [InlineKeyboardButton(text=KIND_LABEL["faculty"], callback_data=f"bi:pick:{abt_id}:f:1")],
        [InlineKeyboardButton(text="♻️ Filtrlarni tozalash", callback_data=f"bi:clear:{abt_id}")],
        [InlineKeyboardButton(text="⬅️ Natijaga qaytish", callback_data=f"bi:close:{abt_id}")],
    ])


def _pick_markup(abt_id: str, kind_code: str, options: list[tuple[str, str]],
                 selected_value: str, page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, -(-len(options) // FILTER_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    start = (page - 1) * FILTER_PAGE_SIZE
    page_options = options[start:start + FILTER_PAGE_SIZE]

    rows = []
    for value, label in page_options:
        prefix = "✅ " if value == selected_value else ""
        rows.append([
            InlineKeyboardButton(
                text=(prefix + label)[:60],
                callback_data=f"bi:set:{abt_id}:{kind_code}:{value}",
            )
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"bi:pick:{abt_id}:{kind_code}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="bi:noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"bi:pick:{abt_id}:{kind_code}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Filtr menyusi", callback_data=f"bi:menu:{abt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_result(chat_id: int, state: FSMContext, abt_id: str,
                         page: int, stale: bool | None = None) -> tuple[str, InlineKeyboardMarkup, int]:
    data = await state.get_data()
    filters = data.get("bi_filters") or {"region": "0", "university": "0", "faculty": "0"}

    res = await ballinfo.get_data(abt_id)
    if "data" not in res:
        raise MandatUnavailable("Ma'lumot eskirgan")

    stale_effective = res.get("stale", False) if stale is None else stale
    text, total, normalized = ballinfo.format_page(
        abt_id,
        res["data"],
        page=page,
        stale=stale_effective,
        filters=filters,
    )
    page = min(max(1, page), total)
    await state.update_data(
        bi_abt=abt_id,
        bi_filters=normalized,
        bi_page=page,
    )
    return text, _page_markup(abt_id, page, total), page


async def _edit_or_send_result(source_message: Message, state: FSMContext,
                               abt_id: str, page: int) -> None:
    text, markup, page = await _render_result(source_message.chat.id, state, abt_id, page)
    data = await state.get_data()
    result_mid = data.get("bi_mid")

    if result_mid:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=source_message.chat.id,
                message_id=result_mid,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
        except Exception:
            logging.debug("Asosiy natija xabarini edit qilib bo'lmadi, yangisini yuboramiz")

    sent = await source_message.answer(text, parse_mode="HTML", reply_markup=markup)
    await state.update_data(bi_mid=sent.message_id, bi_page=page)


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
async def handle_yonalish_id(msg: Message, state: FSMContext):
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
            filters = {"region": "0", "university": "0", "faculty": "0"}
            text, total, normalized = ballinfo.format_page(
                abt_id,
                res["data"],
                page=1,
                stale=res.get("stale", False),
                filters=filters,
            )
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
    if markup is None:
        await answer_safe(msg, text, parse_mode="HTML")
        return

    sent = await msg.answer(text, parse_mode="HTML", reply_markup=markup)
    await state.update_data(
        bi_abt=abt_id,
        bi_filters=normalized,
        bi_page=1,
        bi_mid=sent.message_id,
    )


@yonalish_router.callback_query(F.data.startswith("bi:"))
async def yonalish_callbacks(call: CallbackQuery, state: FSMContext):
    if call.data == "bi:noop":
        await call.answer()
        return

    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    try:
        if action == "page" and len(parts) >= 4:
            abt_id, page = parts[2], int(parts[3])
            await _edit_or_send_result(call.message, state, abt_id, page)
            await call.answer()
            return

        if action == "menu" and len(parts) >= 3:
            abt_id = parts[2]
            data = await state.get_data()
            filters = data.get("bi_filters") or {"region": "0", "university": "0", "faculty": "0"}

            res = await ballinfo.get_data(abt_id)
            if "data" not in res:
                await call.answer("Ma'lumot eskirgan — ID'ni qaytadan yuboring", show_alert=True)
                return
            caption, normalized = ballinfo.filter_caption(res["data"], filters)
            await state.update_data(bi_filters=normalized)
            await call.message.answer(
                "🔎 <b>Filtr menyusi</b>\n\n"
                "Saytdagi kabi 3 bosqichli filtr:\n"
                "1) Hudud\n2) OTM\n3) Yo'nalish\n\n"
                f"<i>Joriy holat: {caption}</i>",
                parse_mode="HTML",
                reply_markup=_filter_menu_markup(abt_id),
            )
            await call.answer()
            return

        if action == "pick" and len(parts) >= 5:
            abt_id, kind_code, page = parts[2], parts[3], int(parts[4])
            kind = CODE_TO_KIND.get(kind_code)
            if kind is None:
                await call.answer()
                return

            data = await state.get_data()
            filters = data.get("bi_filters") or {"region": "0", "university": "0", "faculty": "0"}
            res = await ballinfo.get_data(abt_id)
            if "data" not in res:
                await call.answer("Ma'lumot eskirgan — ID'ni qaytadan yuboring", show_alert=True)
                return

            options, normalized = ballinfo.get_filter_options(res["data"], filters, kind)
            await state.update_data(bi_filters=normalized)
            current = normalized.get(kind, "0")
            await call.message.edit_text(
                f"{KIND_LABEL[kind]} ni tanlang:",
                parse_mode="HTML",
                reply_markup=_pick_markup(abt_id, kind_code, options, current, page),
            )
            await call.answer()
            return

        if action == "set" and len(parts) >= 5:
            abt_id, kind_code, value = parts[2], parts[3], parts[4]
            kind = CODE_TO_KIND.get(kind_code)
            if kind is None:
                await call.answer()
                return

            data = await state.get_data()
            filters = data.get("bi_filters") or {"region": "0", "university": "0", "faculty": "0"}
            filters[kind] = value
            await state.update_data(bi_filters=filters)

            await _edit_or_send_result(call.message, state, abt_id, page=1)
            res = await ballinfo.get_data(abt_id)
            if "data" in res:
                caption, normalized = ballinfo.filter_caption(res["data"], filters)
                await state.update_data(bi_filters=normalized)
                await call.message.edit_text(
                    "🔎 <b>Filtr menyusi</b>\n\n"
                    "Filtr yangilandi. Yana tanlash yoki tozalash mumkin.\n\n"
                    f"<i>Joriy holat: {caption}</i>",
                    parse_mode="HTML",
                    reply_markup=_filter_menu_markup(abt_id),
                )
            await call.answer("Filtr qo'llandi")
            return

        if action == "clear" and len(parts) >= 3:
            abt_id = parts[2]
            await state.update_data(bi_filters={"region": "0", "university": "0", "faculty": "0"})
            await _edit_or_send_result(call.message, state, abt_id, page=1)
            await call.answer("Filtrlar tozalandi")
            return

        if action == "close":
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.answer()
            return

        if action == "pdf" and len(parts) >= 3:
            abt_id = parts[2]
            await call.answer("PDF tayyorlanmoqda...")
            res = await ballinfo.get_data(abt_id)
            if "data" not in res:
                await call.message.answer("❌ PDF uchun ma'lumot topilmadi. ID ni qayta yuboring.")
                return

            pdf_bytes = ballinfo.build_pdf_bytes(abt_id, res["data"])
            await call.message.answer_document(
                BufferedInputFile(pdf_bytes, filename=f"yonalishlar_{abt_id}.pdf"),
                caption="🧾 Filtrlarsiz to'liq yo'nalishlar ro'yxati",
            )
            return

        await call.answer()
    except (MandatBusy, MandatUnavailable):
        await call.answer("Sayt band — birozdan so'ng urinib ko'ring", show_alert=True)
    except Exception as e:
        logging.exception(f"Yo'nalish callback xatoligi: {e}")
        try:
            await call.answer("Xatolik yuz berdi", show_alert=True)
        except Exception:
            pass


@yonalish_router.message(YonalishState.kutish, F.text != "🏅 O'rin aniqlash",
                         F.text != "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def invalid_yonalish_input(msg: Message):
    await msg.answer("✋ Iltimos, faqat 7 xonali ID raqamini yuboring (faqat raqamlar).",
                     reply_markup=await UserPanels.to_back())
