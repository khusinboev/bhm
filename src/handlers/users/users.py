import os
import re
import fitz
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

user_router = Router()

class MainState(StatesGroup):
    natija = State()
    natija2 = State()


@user_router.message(CommandStart())
async def start_cmd1(message: Message):
    await message.answer("<b>Botimizdan foydalanish uchun quyidagi tugmalardan birini tanlang</b>",
                         reply_markup=await UserPanels.main2(),  parse_mode="html")

@user_router.callback_query(F.data == "check", F.message.chat.type == ChatType.PRIVATE)
async def check(call: CallbackQuery):
    user_id = call.from_user.id
    try:
        check_status, channels = await CheckData.check_member(bot, user_id)
        if check_status:
            await call.message.delete()
            await bot.send_message(chat_id=user_id,
                                   text="<b>Botimizdan foydalanish uchun quyidagi tugmalardan birini tanlang</b>",
                                   reply_markup=await UserPanels.main2(),
                                   parse_mode="html")
            try:
                await call.answer()
            except:
                pass
        else:
            try:
                await call.answer(show_alert=True, text="Botimizdan foydalanish uchun barcha kanallarga a'zo bo'ling")
            except:
                try:
                    await call.answer()
                except:
                    pass
    except Exception as e:
        await bot.forward_message(chat_id=ADMIN_ID[0], from_chat_id=call.message.chat.id, message_id=call.message.message_id)
        await bot.send_message(chat_id=ADMIN_ID[0], text=f"Error in check:\n{e}")


@user_router.message(F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE, F.state.in_([MainState.natija, MainState.natija2]))
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())
    try:
        await state.clear()
    except: pass


@user_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    # user_id = message.from_user.id
    # check_status, channels = await CheckData.check_member(bot, user_id)
    # if not check_status:
    #     await message.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
    #                          reply_markup=await CheckData.channels_btn(channels))
    #     return
    # current_dir = os.path.dirname(os.path.abspath(__file__))
    # file_path = os.path.join(current_dir, "havola.txt")
    # with open(file_path, "r", encoding="utf-8") as f:
    #     havola =  f.read().strip()
    # btn = InlineKeyboardMarkup(
    #     inline_keyboard=[
    #         [InlineKeyboardButton(
    #             text="📲 Natijani ko'rish",
    #             web_app=WebAppInfo(url=havola)
    #         )]
    #     ]
    # )
    #await message.answer("<b>👇🏻 Quyidagi tugmani bosib natijangizni ko'rishingiz mumkin</b>", reply_markup=btn,  parse_mode="html")

    from_chat_id = "@Second_Polat"
    message_id = 733
    await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            reply_markup=await UserPanels.to_back(),
        )
    await state.set_state(MainState.natija)


@user_router.message(F.text == "📝 Natijaga buyurtma berish", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message, state: FSMContext):
    await message.answer("Natijangizni buyurtma qilish uchun '<b>Abituriyent ruxsatnomasi</b>'ni <b>PDF</b> faylini yuboring", reply_markup=await UserPanels.main(),  parse_mode="html")
    await state.set_state(MainState.natija)


@user_router.message(F.text.startswith("kirit"), F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_ID))
async def save_link_to_txt(message: Message):
    # Regex orqali havolani ajratamiz
    match = re.search(r"https?://\S+", message.text)
    if match:
        url = match.group()
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, "havola.txt")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f'{url}')
        await message.answer(f"✅ Havola saqlandi:\n{url}")
    else:
        await message.answer("❗ Havola topilmadi. Format: \n\n<code>kirit https://...</code>")