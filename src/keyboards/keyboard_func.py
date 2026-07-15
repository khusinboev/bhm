import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, User

from config import sql, db, bot, ADMIN_ID
from src.db import database


class CheckData:
    @staticmethod
    async def check_member(bot: Bot, user_id: int):
        mandatory = await database.fetchall("SELECT chat_id FROM public.mandatorys")
        if not mandatory:
            return True, []

        channels = []
        for (chat_id,) in mandatory:
            try:
                r = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                # "left" bilan birga kanaldan chetlatilgan ("kicked") ham a'zo emas
                if r.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) and user_id not in ADMIN_ID:
                    channels.append(chat_id)
            except Exception as e:
                logging.warning(f"Kanal a'zoligini tekshirib bo'lmadi (chat_id={chat_id}): {e}")
        return (len(channels) == 0), channels

    @staticmethod
    async def channels_btn(channels: list):
        keyboard = []
        for index, channel_id in enumerate(channels, 1):
            link = await database.fetchone(
                "SELECT username FROM public.mandatorys WHERE chat_id=%s", (channel_id,)
            )
            if link:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"📢 Kanal-{index}",
                        url=link[0]
                    )
                ])
        keyboard.append([InlineKeyboardButton(text="✅Qo'shildim", callback_data="check")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)


class PanelFunc:
    @staticmethod
    async def channel_add(chat_id, link):
        sql.execute(f"INSERT INTO public.mandatorys( chat_id, username ) VALUES({chat_id}, '{link}');")
        db.commit()

    @staticmethod
    async def channel_delete(id):
        sql.execute(f'''DELETE FROM public.mandatorys WHERE chat_id = '{id}' ''')
        db.commit()

    @staticmethod
    async def channel_list():
        sql.execute("SELECT chat_id, username from public.mandatorys")
        str = ''
        for row in sql.fetchall():
            chat_id = row[0]
            try:
                all_details = await bot.get_chat(chat_id=chat_id)
                title = all_details.title
                channel_id = all_details.id
                channel_id = row[1]
                info = all_details.description
                str += f"------------------------------------------------\nKanal useri: > @{all_details.username}\nKamal nomi: > {title}\nKanal id si: > {channel_id}\nKanal haqida: > {info}\n"
            except Exception as e:
                str += f"Kanalni admin qiling\n\nError: {e}"
        return str

    @staticmethod
    async def admin_add(chat_id):
        sql.execute(f"INSERT INTO public.admins( user_id ) VALUES({chat_id});")
        db.commit()

    @staticmethod
    async def admin_delete(id):
        sql.execute(f'''DELETE FROM public.admins WHERE user_id = '{id}' ''')
        db.commit()

    @staticmethod
    async def admin_list():
        sql.execute("SELECT user_id from public.admins")
        str = ""
        for row in sql.fetchall():
            chat_id = row[0]
            try:
                user: User = await bot.get_chat(chat_id)
                username = f"@{user.username}" if user.username else "❌ Topilmadi"
                full_name = user.full_name
                str += f"👤 Foydalanuvchi:\n🔹 Ism: {full_name}\n🔹 Username: {username}\n🔹 ID: <code>{user.id}</code>\n\n"
            except Exception as e:
                str += f"xatolik:\n" + f"🔹 ID: <code>{chat_id}</code>\n\n"
        return str
