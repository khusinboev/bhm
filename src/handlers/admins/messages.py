import asyncio
import os
import aiofiles
import logging
from datetime import datetime

import pytz
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, KeyboardButton, ReplyKeyboardMarkup, BufferedInputFile,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter
)
from config import ADMIN_ID, bot
from src.db import database
from src.keyboards.buttons import AdminPanel

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('broadcast.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

msg_router = Router()

# Bir vaqtda ochiq so'rovlar soni (tarmoq kutishini yashirish uchun) —
# haqiqiy tezlikni bu emas, pastdagi RateLimiter belgilaydi
BROADCAST_CONCURRENCY = 10
semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)

# Telegram'ga haqiqiy chaqiruvlar chegarasi: global limit ~30/s dan xavfsiz
# past, shunda userlarning natija so'rovlariga ham joy qoladi
BROADCAST_RATE_PER_SEC = 15

# Foydalanuvchi oqimi eng yuqori bo'lgan soatlar (Asia/Tashkent) — shu payt
# anons boshlansa admin'dan qo'shimcha tasdiq so'raladi
PEAK_HOURS = range(18, 23)

# Files for logging failed users
FAILED_USERS_FILE = "failed_users.txt"
TEST_FAILED_COPY_FILE = "test_failed_copy.txt"
TEST_FAILED_FORWARD_FILE = "test_failed_forward.txt"


class RateLimiter:
    """Token-bucket: soniyasiga cheklangan sondagina chaqiruv o'tishini kafolatlaydi.

    Concurrency (semaphore)dan farqli — bir nechta so'rov parallel ochiq
    turishi mumkin, lekin haqiqiy Telegram chaqiruvlari doim tekis oqimda.
    """

    def __init__(self, rate_per_sec: float):
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self):
        loop = asyncio.get_event_loop()
        async with self._lock:
            now = loop.time()
            start = max(self._next_slot, now)
            self._next_slot = start + self._interval
            delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)


broadcast_limiter = RateLimiter(BROADCAST_RATE_PER_SEC)


# === STATES (FSM) === #
class MsgState(StatesGroup):
    forward_msg = State()
    send_msg = State()
    test_copy_msg = State()
    test_forward_msg = State()

# === BACK BUTTON === #
markup = ReplyKeyboardMarkup(
    resize_keyboard=True,
    keyboard=[[KeyboardButton(text="🔙Orqaga qaytish")]]
)

# === Fon vazifa holati (bir vaqtda faqat bitta anons) === #
_task: asyncio.Task | None = None
_stats: dict = {}
_pending: dict[str, tuple] = {}  # tasdiq kutayotgan anonslar (cho'qqi soat)


def _new_stats(total: int, kind: str) -> dict:
    return {
        "kind": kind,
        "boshlandi": datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%d.%m %H:%M:%S"),
        "jami": total,
        "yuborildi": 0,
        "xato": 0,
        "tugadi": False,
    }


def _holat_text() -> str:
    if not _stats:
        return "Hali anons boshlanmagan."
    s = _stats
    holat = "tugagan ✅" if s["tugadi"] else "davom etmoqda ⏳"
    bajarildi = s["yuborildi"] + s["xato"]
    return (
        f"📤 <b>Anons holati</b> ({s['kind']}, {s['boshlandi']} da boshlangan, {holat})\n\n"
        f"📦 Jami: {s['jami']}\n"
        f"✅ Yuborildi: {s['yuborildi']}\n"
        f"❌ Xato/bloklagan: {s['xato']}\n"
        f"📊 Progres: {bajarildi}/{s['jami']}"
    )


def _is_peak_hour(now: datetime | None = None) -> bool:
    now = now or datetime.now(pytz.timezone("Asia/Tashkent"))
    return now.hour in PEAK_HOURS


# === LOGGER: Write failed user to file === #
async def log_failed_user(user_id: int, filename: str):
    async with aiofiles.open(filename, mode="a") as f:
        await f.write(f"{user_id}\n")
    logger.info(f"Failed user {user_id} logged to {filename}")

# === SAFE SEND FUNCTIONS === #
async def send_copy_safe(user_id: int, message: Message, semaphore: asyncio.Semaphore, is_test: bool = False, test_filename: str = None):
    async with semaphore:
        for attempt in range(5):
            try:
                await broadcast_limiter.wait()
                sent_msg = await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                if is_test:
                    await bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                logger.info(f"Successfully sent copy to user {user_id}")
                return True
            except TelegramRetryAfter as e:
                logger.warning(f"RetryAfter for user {user_id}: waiting {e.retry_after}s")
                await asyncio.sleep(e.retry_after + 0.5)
            except (TelegramForbiddenError, TelegramNotFound):
                logger.info(f"User {user_id} blocked or not found")
                await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                return False
            except TelegramBadRequest as e:
                if "message to copy not found" in str(e).lower():
                    logger.error(f"Message to copy not found for user {user_id}")
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
                if attempt < 4:
                    logger.warning(f"BadRequest for user {user_id}, attempt {attempt + 1}: {e}")
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"Failed to send copy to user {user_id}: {e}")
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
            except Exception as e:
                logger.error(f"Unexpected error sending copy to {user_id} (attempt {attempt + 1}): {e}")
                if attempt < 4:
                    await asyncio.sleep(2 ** attempt)
                else:
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
        logger.error(f"Failed to send copy to user {user_id} after 5 attempts")
        return False

async def send_forward_safe(user_id: int, message: Message, semaphore: asyncio.Semaphore, is_test: bool = False, test_filename: str = None):
    async with semaphore:
        for attempt in range(5):
            try:
                await broadcast_limiter.wait()
                sent_msg = await bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                if is_test:
                    await bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                logger.info(f"Successfully sent forward to user {user_id}")
                return True
            except TelegramRetryAfter as e:
                logger.warning(f"RetryAfter for user {user_id}: waiting {e.retry_after}s")
                await asyncio.sleep(e.retry_after + 0.5)
            except (TelegramForbiddenError, TelegramNotFound):
                logger.info(f"User {user_id} blocked or not found")
                await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                return False
            except TelegramBadRequest as e:
                if attempt < 4:
                    logger.warning(f"BadRequest for user {user_id}, attempt {attempt + 1}: {e}")
                    await asyncio.sleep(2 ** attempt)
                else:
                    logger.error(f"Failed to send forward to user {user_id}: {e}")
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
            except Exception as e:
                logger.error(f"Unexpected error sending forward to {user_id} (attempt {attempt + 1}): {e}")
                if attempt < 4:
                    await asyncio.sleep(2 ** attempt)
                else:
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
        logger.error(f"Failed to send forward to user {user_id} after 5 attempts")
        return False

# === BROADCAST (fon vazifa, bekor qilinadigan) === #
async def _run(message: Message, user_ids: list[int], send_func, is_test: bool = False, test_filename: str = None):
    global _task
    total = len(user_ids)
    _stats.clear()
    _stats.update(_new_stats(total, "sinov" if is_test else "anons"))

    status_msg = await message.answer("📤 Yuborish boshlandi...")

    filename = test_filename if is_test else FAILED_USERS_FILE
    if os.path.exists(filename):
        os.remove(filename)
        logger.info(f"Cleared log file: {filename}")

    batch_size = 50
    try:
        for i in range(0, total, batch_size):
            batch = user_ids[i:i + batch_size]
            tasks = [send_func(uid, message, semaphore, is_test, test_filename) for uid in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if result is True:
                    _stats["yuborildi"] += 1
                else:
                    _stats["xato"] += 1

            done = min(i + batch_size, total)
            if done % 500 == 0 or done >= total:
                try:
                    await status_msg.edit_text(_holat_text(), parse_mode="html")
                except Exception as e:
                    logger.error(f"Failed to update status message: {e}")

        _stats["tugadi"] = True
        await message.answer(
            "✅ Yakunlandi!\n\n" + _holat_text(),
            parse_mode="html",
            reply_markup=await AdminPanel.admin_msg()
        )
        logger.info(f"Broadcast completed: {_stats['yuborildi']} successful, {_stats['xato']} failed, total: {total}")
    except asyncio.CancelledError:
        _stats["tugadi"] = True
        try:
            await message.answer("⛔ Anons to'xtatildi.\n\n" + _holat_text(), parse_mode="html")
        except Exception:
            pass
        raise
    except Exception as e:
        logger.exception("Broadcastda kutilmagan xato")
        try:
            await message.answer(f"🚨 Anons xato bilan to'xtadi: {e}")
        except Exception:
            pass
    finally:
        if os.path.exists(filename):
            try:
                async with aiofiles.open(filename, "rb") as f:
                    data = await f.read()
                    file = BufferedInputFile(data, filename)
                    await message.answer_document(
                        file,
                        caption=f"❌ {'Sinov' if is_test else 'Xabar'} yuborishda xato bo‘lgan foydalanuvchilar"
                    )
                    logger.info(f"Sent failed users file: {filename}")
            except Exception as e:
                logger.error(f"Failed to send failed-users file: {e}")
        _task = None


async def _start_or_confirm(message: Message, send_func, is_test: bool = False, test_filename: str = None):
    """Anons/sinov boshlaydi. Cho'qqi soatda (18:00–23:00) va bu sinov bo'lmasa,
    admin'dan avval tasdiq so'raydi — shu vaqtda userlar oqimi eng yuqori bo'ladi.
    """
    global _task
    if _task is not None and not _task.done():
        await message.answer("⏳ Hozir allaqachon anons ketmoqda.\n\n" + _holat_text(), parse_mode="html")
        return

    user_ids = await get_user_ids_paginated()
    if not user_ids:
        await message.answer("❗ Yuboriladigan foydalanuvchi topilmadi.")
        return

    if not is_test and _is_peak_hour():
        now_tz = datetime.now(pytz.timezone("Asia/Tashkent"))
        token = f"{message.chat.id}:{message.message_id}"
        _pending[token] = (user_ids, message, send_func, is_test, test_filename)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ha, yuborilsin", callback_data=f"anons_go:{token}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"anons_cancel:{token}"),
        ]])
        await message.answer(
            f"⚠️ Hozir eng band payt ({now_tz.strftime('%H:%M')}) — userlar tomonidan natija "
            f"so'rovlari ko'p bo'layotgan bo'lishi mumkin.\n\n"
            f"{len(user_ids)} ta userga baribir yuborilsinmi?",
            reply_markup=kb
        )
        return

    _task = asyncio.create_task(_run(message, user_ids, send_func, is_test, test_filename))
    await message.answer(
        f"🚀 Yuborish fonda boshlandi ({len(user_ids)} ta user).\n"
        f"Holat: /anons_holat\nTo'xtatish: /anons_stop"
    )


@msg_router.callback_query(F.data.startswith("anons_go:"), F.from_user.id.in_(ADMIN_ID))
async def anons_confirm(call: CallbackQuery):
    global _task
    token = call.data.split(":", 1)[1]
    pending = _pending.pop(token, None)
    if not pending:
        await call.answer("Bu so'rov eskirgan.", show_alert=True)
        return
    if _task is not None and not _task.done():
        await call.answer("Boshqa anons allaqachon ketmoqda.", show_alert=True)
        return

    user_ids, message, send_func, is_test, test_filename = pending
    _task = asyncio.create_task(_run(message, user_ids, send_func, is_test, test_filename))
    try:
        await call.message.edit_text(f"🚀 Tasdiqlandi — yuborish boshlandi ({len(user_ids)} ta user).")
    except Exception:
        pass
    await call.answer()


@msg_router.callback_query(F.data.startswith("anons_cancel:"), F.from_user.id.in_(ADMIN_ID))
async def anons_cancel(call: CallbackQuery):
    token = call.data.split(":", 1)[1]
    _pending.pop(token, None)
    try:
        await call.message.edit_text("❌ Anons bekor qilindi.")
    except Exception:
        pass
    await call.answer()


@msg_router.message(Command("anons_holat"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def anons_status(message: Message):
    await message.answer(_holat_text(), parse_mode="html")


@msg_router.message(Command("anons_stop"), F.from_user.id.in_(ADMIN_ID), F.chat.type == ChatType.PRIVATE)
async def anons_stop_cmd(message: Message):
    global _task
    if _task is None or _task.done():
        await message.answer("Hozir faol anons yo'q.")
        return
    _task.cancel()
    await message.answer("⛔ To'xtatish so'raldi — yakuniy holat birozdan so'ng keladi.")


# === DATABASE PAGINATION === #
async def get_user_ids_paginated(batch_size: int = 5000):
    offset = 0
    user_ids = []
    while True:
        rows = await database.fetchall(
            "SELECT user_id FROM public.accounts ORDER BY id LIMIT %s OFFSET %s",
            (batch_size, offset)
        )
        if not rows:
            break
        user_ids.extend(row[0] for row in rows)
        offset += batch_size
        logger.info(f"Fetched {len(rows)} user IDs at offset {offset}")
    return user_ids

# === HANDLERS === #
@msg_router.message(F.text == "✍Xabarlar", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def panel_handler(message: Message) -> None:
    await message.answer("Xabarlar bo'limi!", reply_markup=await AdminPanel.admin_msg())
    logger.info(f"Admin {message.from_user.id} accessed messages panel")

@msg_router.message(F.text == "📨Forward xabar yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def start_forward(message: Message, state: FSMContext):
    await message.answer("Forward yuboriladigan xabarni yuboring", reply_markup=markup)
    await state.set_state(MsgState.forward_msg)
    logger.info(f"Admin {message.from_user.id} started forward message")

@msg_router.message(MsgState.forward_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def send_forward_to_all(message: Message, state: FSMContext):
    await state.clear()
    await _start_or_confirm(message, send_forward_safe, is_test=False)
    logger.info(f"Admin {message.from_user.id} requested forward broadcast")

@msg_router.message(F.text == "📬Oddiy xabar yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def start_text_send(message: Message, state: FSMContext):
    await message.answer("Yuborilishi kerak bo'lgan xabarni yuboring", reply_markup=markup)
    await state.set_state(MsgState.send_msg)
    logger.info(f"Admin {message.from_user.id} started copy message")

@msg_router.message(MsgState.send_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def send_text_to_all(message: Message, state: FSMContext):
    await state.clear()
    await _start_or_confirm(message, send_copy_safe, is_test=False)
    logger.info(f"Admin {message.from_user.id} requested copy broadcast")

@msg_router.message(F.text == "🧪Sinov: Copy yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def test_copy_broadcast(message: Message, state: FSMContext):
    await message.answer("🧪 Sinov: Oddiy xabarni yuboring (copy), yuboriladi va darhol o‘chiriladi:")
    await state.set_state(MsgState.test_copy_msg)
    logger.info(f"Admin {message.from_user.id} started test copy broadcast")

@msg_router.message(MsgState.test_copy_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def handle_test_copy(message: Message, state: FSMContext):
    await state.clear()
    await _start_or_confirm(message, send_copy_safe, is_test=True, test_filename=TEST_FAILED_COPY_FILE)
    logger.info(f"Admin {message.from_user.id} requested test copy broadcast")

@msg_router.message(F.text == "🧪Sinov: Forward yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def test_forward_broadcast(message: Message, state: FSMContext):
    await message.answer("🧪 Sinov: Forward xabar yuboring, darhol o‘chiriladi:")
    await state.set_state(MsgState.test_forward_msg)
    logger.info(f"Admin {message.from_user.id} started test forward broadcast")

@msg_router.message(MsgState.test_forward_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def handle_test_forward(message: Message, state: FSMContext):
    await state.clear()
    await _start_or_confirm(message, send_forward_safe, is_test=True, test_filename=TEST_FAILED_FORWARD_FILE)
    logger.info(f"Admin {message.from_user.id} requested test forward broadcast")

@msg_router.message(F.text == "🔙Orqaga qaytish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Orqaga qaytildi", reply_markup=await AdminPanel.admin_msg())
    logger.info(f"Admin {message.from_user.id} returned to menu")
