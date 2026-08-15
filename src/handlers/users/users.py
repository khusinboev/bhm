import logging
import time

from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery

from config import bot, ADMIN_ID
from src.db import database
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData
from src.utils import rate_limit, result_service
from src.utils.mandat_parser import MandatBusy, MandatUnavailable
from src.utils.safe_send import answer_safe

user_router = Router()

# Adminni xato xabarlari bilan ko'mib tashlamaslik uchun.
#
# Ilgari "✅ Tekshirish" handleri HAR QANDAY xatoda adminga xabar yozardi.
# Ommaviy tarqatishdan keyin bu ikki tomonlama zarar berdi:
#   1) admin minglab bir xil xabar oldi ("bot was blocked by the user" —
#      foydalanuvchi botni bloklagani odatiy holat, xato emas);
#   2) har bir o'sha xabar botning Telegram kvotasidan (~30 xabar/soniya)
#      yeb, asosiy sekinlikni yanada kuchaytirdi.
#
# Endi: kutilgan xatolar umuman xabar qilinmaydi, kutilmaganlari esa
# turi bo'yicha throttle qilinadi.
ADMIN_ALERT_INTERVAL = 300  # bir xil turdagi xato haqida ko'pi bilan 5 daqiqada bir marta
_last_admin_alert: dict[str, float] = {}


async def _alert_admin(prefix: str, exc: Exception) -> None:
    """Kutilmagan xato haqida adminga xabar — turi bo'yicha throttle bilan."""
    key = f"{prefix}:{type(exc).__name__}"
    now = time.monotonic()
    if now - _last_admin_alert.get(key, 0.0) < ADMIN_ALERT_INTERVAL:
        return
    _last_admin_alert[key] = now
    try:
        await bot.send_message(
            chat_id=ADMIN_ID[0],
            text=f"{prefix}\n{type(exc).__name__}: {exc}",
        )
    except Exception:
        logging.warning("Adminga ogohlantirish yuborib bo'lmadi", exc_info=True)


class MainState(StatesGroup):
    natija2 = State()


# /start endi royxat_router tomonidan ishlanadi (viloyat + telefon so'raladi)


@user_router.callback_query(F.data == "check", F.message.chat.type == ChatType.PRIVATE)
async def check(call: CallbackQuery):
    user_id = call.from_user.id
    try:
        check_status, channels = await CheckData.check_member(bot, user_id)
        if check_status:
            await bot.send_message(chat_id=user_id,
                                   text="<b>📄 Natijani ko'rish uchun quyidagi tugmalardan birini tanlang:</b>",
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
    except TelegramForbiddenError:
        # Foydalanuvchi botni bloklagan — mutlaqo odatiy holat, xato emas.
        logging.info("Bloklagan foydalanuvchiga javob yuborilmadi (user_id=%s)", user_id)
    except TelegramRetryAfter as e:
        # Telegram tezlik chegarasi — o'zi tiklanadi, adminni bezovta qilmaymiz.
        logging.warning("check: Telegram tezlik chegarasi, %s s kutish kerak", e.retry_after)
    except (TelegramNetworkError, TelegramBadRequest) as e:
        # Tarmoq uzilishi yoki eskirgan callback ("query is too old") — kutilgan.
        logging.warning("check: vaqtinchalik Telegram xatosi: %s", e)
    except Exception as e:
        # Faqat haqiqatan kutilmagan xatolar adminga boradi (throttle bilan).
        logging.exception("check callback xatoligi")
        await _alert_admin("Error in check:", e)


@user_router.message(F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def back_to_main(message: Message, state: FSMContext):
    try:
        await state.clear()
    except: pass
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())


@user_router.message(F.text == "📝 Mandat natijasiga buyurtma berish", F.chat.type == ChatType.PRIVATE)
async def ask_id(message: Message, state: FSMContext):
    check_status, channels = await CheckData.check_member(bot, message.from_user.id)
    if not check_status:
        await message.answer(
            "❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
            reply_markup=await CheckData.channels_btn(channels)
        )
        return
    await message.answer("📝 Iltimos, 7 xonali ID raqamingizni yuboring:", reply_markup=await UserPanels.main())
    await state.set_state(MainState.natija2)


@user_router.message(F.text == "📁 Mening buyurtmalarim", F.chat.type == ChatType.PRIVATE)
async def my_orders(message: Message):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer(
            "❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
            parse_mode='html',
            reply_markup=await CheckData.channels_btn(channels)
        )
        return

    # So‘nggi 6 ta buyurtma
    records = await database.fetchall("""
        SELECT abt_id, abt_name, umumiy_ball, umumiy_orn, id
        FROM bhm
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 6
    """, (user_id,))

    if not records:
        await message.answer(
            "❗ Sizda hali hech qanday buyurtma mavjud emas.\n\n"
            "Natijangizni buyurtma qilish uchun '<b>ID raqamingiz</b>'ni yuboring.",
            parse_mode="html"
        )
        return

    text_header = f"<b>👇 Sizning so‘nggi {len(records)} ta buyurtmangiz:</b>\n\n"

    body = ""
    for abt_id, fio, umumiy_ball, umumiy_orn, order_num in records:
        ball_txt = umumiy_ball if umumiy_ball is not None else "hali e'lon qilinmagan"
        body += (
            f"✅ <b>{abt_id}</b> ID raqamli mandat natijasiga buyurtma qabul qilindi\n"
            f"📑 Buyurtma tartib raqami: {int(order_num) + 100}\n"
            f"F.I.SH: {fio}\n"
            f"Umumiy ball: {ball_txt}\n"
        )
        if umumiy_orn:
            body += f"Mandat saytidagi o‘rningiz: {umumiy_orn}\n"
        body += "\n"

    await message.answer(text_header + body, parse_mode="html")


@user_router.message(MainState.natija2, F.text.regexp(r"^\d{7}$"), F.chat.type == ChatType.PRIVATE)
async def handle_id(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not rate_limit.allow(user_id):
        await message.answer("⏳ Juda tez-tez so'rov yubordingiz. Iltimos, bir necha soniya kutib qayta urining.")
        return
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer(
            "❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
            reply_markup=await CheckData.channels_btn(channels)
        )
        return

    abt_id = message.text.strip()
    # Avval bazadan qidiramiz
    record = await database.fetchone("""
        SELECT abt_id, abt_name, umumiy_ball, umumiy_orn, id
        FROM bhm
        WHERE user_id = %s AND abt_id = %s
    """, (user_id, abt_id))

    if record:
        abt_id, fio, umumiy_ball, umumiy_orn, order_number = record
        if umumiy_ball is None:
            # Buyurtmadan keyin natija chiqqan bo'lishi mumkin — omborni tekshiramiz
            fresh = await database.fetchone(
                "SELECT umumiy_ball FROM natijalar WHERE abt_id = %s", (abt_id,)
            )
            if fresh and fresh[0] is not None:
                umumiy_ball = fresh[0]
    else:
        # Bazada yo'q — resolver orqali (avval ombor/kesh, kerak bo'lsa sayt).
        # MUHIM: sayt javob bermasa ham buyurtma baribir saqlanadi — natija
        # e'lon qilinganda /tarqat uni topib foydalanuvchiga yuboradi.
        loading = await message.answer("🔍 Ma'lumotlar olinmoqda, kuting...")
        info = None
        try:
            info = await result_service.get_result(abt_id)
        except (MandatBusy, MandatUnavailable):
            logging.info(f"Sayt javob bermadi, buyurtma baribir saqlanadi (ID={abt_id})")
        except Exception:
            logging.exception(f"Buyurtma uchun ma'lumot olishda xatolik (ID={abt_id})")

        try: await loading.delete()
        except: pass

        fio = info["fio"] if info else None
        umumiy_ball = (info["umumiy_ball"].replace(",", ".")
                       if info and info.get("umumiy_ball") else None)
        umumiy_orn = None  # 2026 saytida natijalar jadvali yo'q — o'rin ko'rsatilmaydi

        inserted = await database.fetchone("""
            INSERT INTO bhm (user_id, abt_id, abt_name, umumiy_ball, umumiy_orn, abt_seriya, abt_pinfl, abt_date)
            VALUES (%s, %s, %s, %s, %s, '', '', NOW())
            ON CONFLICT (user_id, abt_id) DO NOTHING
            RETURNING id
        """, (user_id, abt_id, fio, umumiy_ball, umumiy_orn))

        if inserted:
            order_number = inserted[0]
        else:
            row = await database.fetchone(
                "SELECT id FROM bhm WHERE user_id = %s AND abt_id = %s", (user_id, abt_id)
            )
            if row is None:
                await message.answer("❌ Buyurtmani saqlashda xatolik yuz berdi. Qayta urinib ko'ring.")
                return
            order_number = row[0]

    # Foydalanuvchiga javob
    ball_line = (f"🎓 Umumiy ball: <b>{umumiy_ball}</b>\n"
                 if umumiy_ball is not None else "")
    orn_line = f"📊 Mandat saytidagi o‘rningiz: {umumiy_orn}\n" if umumiy_orn else ""
    # F.I.SH hali noma'lum bo'lishi mumkin (sayt javob bermagan bo'lsa)
    kimga = f"<b>{fio}</b>, sizning" if fio else "Sizning"
    text = (
        f"✅ {kimga} <b>{abt_id}</b> raqamli yakuniy mandat "
        f"natijangiz uchun buyurtmangiz qabul qilindi!\n\n"
        f"📑 Buyurtma tartib raqami: <b>{int(order_number) + 100}</b>\n"
        f"{ball_line}{orn_line}\n"
        f"⏳ <b>Yakuniy mandat natijalari e'lon qilinishi bilan sizga "
        f"avtomatik xabar beriladi.</b>\n\n"
        f"✔️ Buyurtma @mandat_uzbmbbot tomonidan amalga oshirilmoqda."
    )

    await answer_safe(message, text, parse_mode="html")


@user_router.message(MainState.natija2, F.text != "📊 Natija",
                     F.text != "🎯 Balingizga mos yo'nalish",
                     F.text != "📊 MANDAT NATIJASI", F.text != "📊 Mandat saytdagi o'rni", F.chat.type == ChatType.PRIVATE)
async def invalid_input(message: Message):
    # Boshqa bo'lim tugmalari bu yerda ushlanmaydi — o'z handlerlariga o'tib ketadi
    await message.answer("✋ Iltimos, faqat 7 xonali ID raqamini kiriting (faqat raqamlar).",
                         reply_markup=await UserPanels.main())
