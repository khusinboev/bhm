import logging

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, User

from config import sql, db, bot, ADMIN_ID
from src.utils import channels_cache

# A'zolik keshi: faqat musbat natija keshlanadi, shunda "Qo'shildim" bosilganda
# yangi a'zolik darhol ko'rinadi, a'zolar esa TTL davomida qayta tekshirilmaydi.
# Redis'da turgani uchun bot restart bo'lsa ham kesh saqlanib qoladi —
# har restartdan keyin butun auditoriyaga getChatMember bo'roni bo'lmaydi.
_MEMBER_TTL = 120
_MEMBER_PREFIX = "mandat:member:"
_member_redis = aioredis.Redis(host="localhost", port=6379, db=1, decode_responses=True)


async def _is_member_cached(user_id: int) -> bool:
    try:
        return bool(await _member_redis.exists(_MEMBER_PREFIX + str(user_id)))
    except Exception as e:
        logging.warning(f"A'zolik keshini Redis'dan o'qib bo'lmadi: {e}")
        return False  # kesh noaniq — haqiqiy tekshiruvga o'tamiz


async def _mark_member_cached(user_id: int) -> None:
    try:
        await _member_redis.set(_MEMBER_PREFIX + str(user_id), "1", ex=_MEMBER_TTL)
    except Exception as e:
        logging.warning(f"A'zolik keshini Redis'ga yozib bo'lmadi: {e}")


class CheckData:
    @staticmethod
    async def check_member(bot: Bot, user_id: int):
        if await _is_member_cached(user_id):
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
            await _mark_member_cached(user_id)
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
