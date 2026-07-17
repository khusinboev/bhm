from aiogram import types
from config import db, sql


async def create_all_base():
    sql.execute("""CREATE TABLE IF NOT EXISTS public.accounts
    (
        id SERIAL NOT NULL,
        user_id BIGINT NOT NULL,
        lang_code CHARACTER VARYING(10),
        date TIMESTAMP DEFAULT now(),
        CONSTRAINT accounts_pkey PRIMARY KEY (id)
    )""")
    db.commit()

    # Middleware'dagi "WHERE NOT EXISTS ... user_id" tekshiruvi (Redis known-users
    # keshi noaniq javob bergan holatlarda) shu indekssiz katta jadvalda sekin bo'lardi
    sql.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON public.accounts (user_id)")
    db.commit()

    sql.execute("""CREATE TABLE IF NOT EXISTS public.mandatorys
    (
        id SERIAL NOT NULL,
        chat_id bigint NOT NULL,
        title character varying,
        username character varying,
        types character varying,
        CONSTRAINT channels_pkey PRIMARY KEY (id)
    )""")
    db.commit()

    sql.execute("""CREATE TABLE IF NOT EXISTS public.admins
    (
        id SERIAL NOT NULL,
        user_id BIGINT NOT NULL,
        date TIMESTAMP DEFAULT now(),
        CONSTRAINT admins_pkey PRIMARY KEY (id)
    )""")
    db.commit()

    sql.execute("""CREATE TABLE IF NOT EXISTS bhm (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        abt_id VARCHAR(20) NOT NULL,
        abt_name TEXT,
        abt_seriya VARCHAR(20),
        abt_pinfl VARCHAR(20),
        abt_date DATE,
        umumiy_ball NUMERIC,
        umumiy_orn INT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, abt_id)
    );""")
    db.commit()

    # Yakuniy natijalar ombori: ID bo'yicha bir marta olinadi, keyin doim shu yerdan
    sql.execute("""CREATE TABLE IF NOT EXISTS public.natijalar (
        abt_id VARCHAR(20) PRIMARY KEY,
        fio TEXT,
        umumiy_ball NUMERIC,
        result_json JSONB NOT NULL,
        found_at TIMESTAMP DEFAULT NOW()
    );""")
    db.commit()

    # Tarqatish uchun: natija userga yuborilgan vaqt
    sql.execute("ALTER TABLE bhm ADD COLUMN IF NOT EXISTS notified_at TIMESTAMP;")
    db.commit()


