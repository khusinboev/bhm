import asyncio
import os
import aiofiles
from aiogram import Router, F, Bot
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, BufferedInputFile
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter
)
from config import ADMIN_ID, sql, bot
from src.keyboards.buttons import AdminPanel

msg_router = Router()

# Semaphore to limit concurrent requests
semaphore = asyncio.Semaphore(20)

# Files for logging failed users
FAILED_USERS_FILE = "failed_users.txt"
TEST_FAILED_COPY_FILE = "test_failed_copy.txt"
TEST_FAILED_FORWARD_FILE = "test_failed_forward.txt"

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

# === LOGGER: Write failed user to file === #
async def log_failed_user(user_id: int, filename: str):
    async with aiofiles.open(filename, mode="a") as f:
        await f.write(f"{user_id}\n")

# === SAFE SEND FUNCTIONS === #
async def send_copy_safe(user_id: int, message: Message, semaphore: asyncio.Semaphore, is_test: bool = False, test_filename: str = None):
    async with semaphore:
        for attempt in range(5):
            try:
                sent_msg = await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                if is_test:
                    await bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except (TelegramForbiddenError, TelegramNotFound):
                await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                return False
            except TelegramBadRequest as e:
                if "message to copy not found" in str(e):
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
                else:
                    if attempt < 4:
                        await asyncio.sleep(2)
                    else:
                        await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                        return False
            except Exception as e:
                print(f"Error sending copy to {user_id}: {e}")
                if attempt < 4:
                    await asyncio.sleep(2)
                else:
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
    return False

async def send_forward_safe(user_id: int, message: Message, semaphore: asyncio.Semaphore, is_test: bool = False, test_filename: str = None):
    async with semaphore:
        for attempt in range(5):
            try:
                sent_msg = await bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )
                if is_test:
                    await bot.delete_message(chat_id=user_id, message_id=sent_msg.message_id)
                return True
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except (TelegramForbiddenError, TelegramNotFound):
                await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                return False
            except TelegramBadRequest as e:
                if attempt < 4:
                    await asyncio.sleep(2)
                else:
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
            except Exception as e:
                print(f"Error sending forward to {user_id}: {e}")
                if attempt < 4:
                    await asyncio.sleep(2)
                else:
                    await log_failed_user(user_id, test_filename if is_test else FAILED_USERS_FILE)
                    return False
    return False

# === BROADCAST FUNCTIONS === #
async def broadcast(user_ids: list[int], message: Message, send_func, is_test: bool = False, test_filename: str = None):
    total = len(user_ids)
    success = 0
    failed = 0
    status_msg = await message.answer("📤 Yuborish boshlandi...")
    batch_size = 1000

    for i in range(0, total, batch_size):
        batch = user_ids[i:i + batch_size]
        tasks = [send_func(uid, message, semaphore, is_test, test_filename) for uid in batch]
        results = await asyncio.gather(*tasks)
        for result in results:
            if result:
                success += 1
            else:
                failed += 1
        try:
            await status_msg.edit_text(
                f"📬 Xabar yuborilmoqda...\n\n"
                f"✅ Yuborilgan: {success} ta\n"
                f"❌ Yuborilmagan: {failed} ta\n"
                f"📦 Jami: {total} ta\n"
                f"📊 Progres: {min(i + batch_size, total)}/{total}"
            )
        except Exception as e:
            print(f"Holatni yangilashda xato: {e}")
        await asyncio.sleep(1)

    await message.answer(
        f"✅ Xabar yuborildi\n ķ\n\n"
        f"📤 Yuborilgan: {success} ta\n"
        f"❌ Yuborilmagan: {failed} ta",
        reply_markup=await AdminPanel.admin_msg()
    )

    if is_test and os.path.exists(test_filename):
        async with aiofiles.open(test_filename, "rb") as f:
            data = await f.read()
            file = BufferedInputFile(data, test_filename)
            await message.answer_document(file, caption="❌ Sinov yuborishda xato bo‘lganlar")

# === HANDLERS === #
@msg_router.message(F.text == "✍Xabarlar", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def panel_handler(message: Message) -> None:
    await message.answer("Xabarlar bo'limi!", reply_markup=await AdminPanel.admin_msg())

@msg_router.message(F.text == "📨Forward xabar yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def start_forward(message: Message, state: FSMContext):
    await message.answer("Forward yuboriladigan xabarni yuboring", reply_markup=markup)
    await state.set_state(MsgState.forward_msg)

@msg_router.message(MsgState.forward_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def send_forward_to_all(message: Message, state: FSMContext):
    await state.clear()
    sql.execute("SELECT user_id FROM public.accounts")
    user_ids = [row[0] for row in sql.fetchall()]
    await broadcast(user_ids, message, send_forward_safe)

@msg_router.message(F.text == "📬Oddiy xabar yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def start_text_send(message: Message, state: FSMContext):
    await message.answer("Yuborilishi kerak bo'lgan xabarni yuboring", reply_markup=markup)
    await state.set_state(MsgState.send_msg)

@msg_router.message(MsgState.send_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def send_text_to_all(message: Message, state: FSMContext):
    await state.clear()
    sql.execute("SELECT user_id FROM public.accounts")
    user_ids = [row[0] for row in sql.fetchall()]
    await broadcast(user_ids, message, send_copy_safe)

@msg_router.message(F.text == "🧪Sinov: Copy yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def test_copy_broadcast(message: Message, state: FSMContext):
    if os.path.exists(TEST_FAILED_COPY_FILE):
        os.remove(TEST_FAILED_COPY_FILE)
    await message.answer("🧪 Sinov: Oddiy xabarni yuboring (copy), yuboriladi va darhol o‘chiriladi:")
    await state.set_state(MsgState.test_copy_msg)

@msg_router.message(MsgState.test_copy_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def handle_test_copy(message: Message, state: FSMContext):
    await state.clear()
    sql.execute("SELECT user_id FROM public.accounts")
    user_ids = [row[0] for row in sql.fetchall()]
    await broadcast(user_ids, message, send_copy_safe, is_test=True, test_filename=TEST_FAILED_COPY_FILE)

@msg_router.message(F.text == "🧪Sinov: Forward yuborish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def test_forward_broadcast(message: Message, state: FSMContext):
    if os.path.exists(TEST_FAILED_FORWARD_FILE):
        os.remove(TEST_FAILED_FORWARD_FILE)
    await message.answer("🧪 Sinov: Forward xabar yuboring, darhol o‘chiriladi:")
    await state.set_state(MsgState.test_forward_msg)

@msg_router.message(MsgState.test_forward_msg, F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def handle_test_forward(message: Message, state: FSMContext):
    await state.clear()
    sql.execute("SELECT user_id FROM public.accounts")
    user_ids = [row[0] for row in sql.fetchall()]
    await broadcast(user_ids, message, send_forward_safe, is_test=True, test_filename=TEST_FAILED_FORWARD_FILE)

@msg_router.message(F.text == "🔙Orqaga qaytish", F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Orqaga qaytildi", reply_markup=await AdminPanel.admin_msg())