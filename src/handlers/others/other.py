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
        "<b>Botimizdan foydalanishni davom etishingiz mumkin, quyidagi tugmalardan birini tanlab davom etishingiz mumkin</b>",
        reply_markup=await UserPanels.main2(), parse_mode="html")
