import hashlib
import os

import psycopg2
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

dbtype = bool(os.getenv("DBTYPE"))
DB_TYPE = "sqlite" if dbtype else "postgres"
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

DB_CONFIG = {
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "host": DB_HOST,
    "port": DB_PORT
}
db = psycopg2.connect(
    database=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
db.autocommit = True
sql = db.cursor()

ADMIN_ID = ADMINS = [int(admin_id) for admin_id in os.getenv("ADMINS_ID").split(",")]

# Bir nechta bot nusxasi (klon) bitta Redis serverida ishlaganda bir-birining
# kalitlariga aralashmasligi uchun: har nusxa o'z REDIS_DB raqamiga ega bo'ladi
REDIS_DB = int(os.getenv("REDIS_DB", "1"))

# === Webhook rejimi (USE_WEBHOOK=1 bo'lsa polling o'rniga ishlaydi) ===
# Yo'l va secret standart holda token hash'idan olinadi — .env'da faqat
# USE_WEBHOOK=1 deyish kifoya; xohlasa alohida override qilish mumkin.
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "0").lower() in ("1", "true", "yes")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "talim24.uz")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
_token_hash = hashlib.sha256(BOT_TOKEN.encode()).hexdigest() if BOT_TOKEN else ""
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", f"/wh/{_token_hash[:24]}")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", _token_hash[32:64])
WEBHOOK_SSL_CERT = os.getenv("WEBHOOK_SSL_CERT", "/etc/letsencrypt/live/talim24.uz/fullchain.pem")
WEBHOOK_SSL_KEY = os.getenv("WEBHOOK_SSL_KEY", "/etc/letsencrypt/live/talim24.uz/privkey.pem")


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(link_preview_is_disabled=True))
# RedisStorage: bot qayta ishga tushganda foydalanuvchi holatlari (FSM) yo'qolmaydi.
storage = RedisStorage.from_url(f"redis://localhost:6379/{REDIS_DB}", state_ttl=86400, data_ttl=86400)
dp = Dispatcher(storage=storage)