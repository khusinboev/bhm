import fitz
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

user_router = Router()


@user_router.message(CommandStart())
async def start_cmd1(message: Message):
    await message.answer("<b>Botimizdan foydalanishni davom etishingiz mumkin, quyidagi tugmalardan birini tanlab davom etishingiz mumkin</b>",
                         reply_markup=await UserPanels.main2(),  parse_mode="html")

@user_router.callback_query(F.data == "check", F.message.chat.type == ChatType.PRIVATE)
async def check(call: CallbackQuery):
    user_id = call.from_user.id
    try:
        check_status, channels = await CheckData.check_member(bot, user_id)
        if check_status:
            await call.message.delete()
            await bot.send_message(chat_id=user_id,
                                   text="<b>Botimizdan foydalanishni davom etishingiz mumkin, quyidagi tugmalardan birini tanlab davom etishingiz mumkin</b>",
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


@user_router.message(F.text == "🔙 Ortga", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message):
    await message.answer("Bosh menu", reply_markup=await UserPanels.main2())


@user_router.message(F.text == "📊 Natija", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message):
    user_id = message.from_user.id
    check_status, channels = await CheckData.check_member(bot, user_id)
    if not check_status:
        await message.answer("❗ Iltimos, quyidagi kanallarga a’zo bo‘ling:",
                             reply_markup=await CheckData.channels_btn(channels))
        return
    btn = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📲 Natijani ko'rish",
                web_app=WebAppInfo(url="https://mandat.uzbmb.uz/")
            )]
        ]
    )
    await message.answer("<b>👇🏻 Quyidagi tugmani bosib natijangizni ko'rishingiz mumkin</b>", reply_markup=btn)


@user_router.message(F.text == "📝 Natijaga buyurtma berish", F.chat.type == ChatType.PRIVATE)
async def show_orders(message: Message):
    await message.answer("Natijangizni buyurtma qilish uchun '<b>Abituriyent ruxsatnomasi</b>'ni <b>PDF</b> faylini yuboring", reply_markup=await UserPanels.main())
