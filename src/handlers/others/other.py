from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from config import bot
from src.keyboards.buttons import UserPanels

other_router = Router()
# some code...

@other_router.message()
async def chosen_lang(message: Message, state: FSMContext):
    try:
        await message.delete()
        await state.clear()
    except: pass
    await message.answer(
        "Natijangizni buyurtma qilish uchun <b>'Abituriyent ruxsatnomasi'</b>ni <b>PDF</b> faylini yuboring",
        reply_markup=await UserPanels.main(), parse_mode="html")
