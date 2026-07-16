import logging
import time

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, User

from config import sql, db, bot, ADMIN_ID
from src.db import database
from src.utils import channels_cache

# A'zolik keshi: faqat musbat natija keshlanadi, shunda "Qo'shildim" bosilganda
# yangi a'zolik darhol ko'rinadi, a'zolar esa TTL davomida qayta tekshirilmaydi
_MEMBER_TTL = 120
_member_cache: dict[int, float] = {}  # user_id -> muddati (monotonic)


class CheckData:
    @staticmethod
    async def check_member(bot: Bot, user_id: int):
        now = time.monotonic()

        if _member_cache.get(user_id, 0.0) > now:
            return True, []

        # Kanallar ro'yxati Redis'dan (Postgres'ga faqat kesh bo'sh bo'lsa boriladi)
        mandatory = await channels_cache.get_channels()
        if not mandatory:
            return True, []

        channels = []
        for chat_id, _username in mandatory:
            try:
                r = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                # "left" bilan birga kanaldan chetlatilgan ("kicked") ham a'zo emas
                if r.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) and user_id not in ADMIN_ID:
                    channels.append(chat_id)
            except Exception as e:
                logging.warning(f"Kanal a'zoligini tekshirib bo'lmadi (chat_id={chat_id}): {e}")

        ok = len(channels) == 0
        if ok:
            if len(_member_cache) > 50_000:
                for uid in [u for u, exp in _member_cache.items() if exp <= now]:
                    _member_cache.pop(uid, None)
            _member_cache[user_id] = now + _MEMBER_TTL
        return ok, channels

    @staticmethod
    async def channels_btn(channels: list):
        # Username'lar ham Redis'dagi ro'yxatdan olinadi
        usernames = {c[0]: c[1] for c in await channels_cache.get_channels()}
        keyboard = []
        for index, channel_id in enumerate(channels, 1):
            link = usernames.get(channel_id)
            if link:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"📢 Kanal-{index}",
                        url=link
                    )
                ])
        keyboard.append([InlineKeyboardButton(text="✅Qo'shildim", callback_data="check")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)


class PanelFunc:
    @staticmethod
    async def channel_add(chat_id, link):
        sql.execute(f"INSERT INTO public.mandatorys( chat_id, username ) VALUES({chat_id}, '{link}');")
        db.commit()
        await channels_cache.refresh()  # user oqimi darhol yangi ro'yxatni ko'radi

    @staticmethod
    async def channel_delete(id):
        sql.execute(f'''DELETE FROM public.mandatorys WHERE chat_id = '{id}' ''')
        db.commit()
        await channels_cache.refresh()

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
