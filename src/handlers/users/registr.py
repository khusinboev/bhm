import fitz
from aiogram import Router, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from config import bot, ADMIN_ID, sql, db
from src.keyboards.buttons import UserPanels
from src.keyboards.keyboard_func import CheckData

registr_router = Router()


@registr_router.message(CommandStart())
async def start_cmd1(message: Message):
    await message.answer("<b>Botimizdan foydalanishni davom etishingiz mumkin, quyidagi tugmalardan birini tanlab davom etishingiz mumkin</b>",
                         reply_markup=await UserPanels.main2(),  parse_mode="html")

