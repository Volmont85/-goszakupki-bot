import asyncio, os, re, json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
import asyncpg
import aioredis

TOKEN = os.getenv("TELEGRAM_TOKEN")
PG_DSN = os.getenv("POSTGRES_URL")
REDIS_URL = os.getenv("REDIS_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- helpers ---
async def setup_storage():
    pg = await asyncpg.connect(PG_DSN)
    rd = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return pg, rd

def valid_zakupka(num): return num.isdigit() and len(num) in (11, 19)
def valid_inn(num): return num.isdigit() and len(num) in (10, 12)
def want_add(txt): return any(w in txt.lower() for w in ['да','давай','добав'])
def agree(txt): return txt.lower() in ['да','ага','ок']

# --- handlers ---
@dp.message(CommandStart())
async def start(msg: types.Message):
    await msg.answer("Привет! Отправь номер закупки (11 или 19 цифр).")

@dp.message(F.text)
async def any_text(msg: types.Message):
    text = msg.text.strip()
    pg, rd = await setup_storage()
    state = await rd.get(f"state:{msg.from_user.id}") or "await_zakupka"

    if state == "await_zakupka":
        if not valid_zakupka(text):
            await msg.answer("Проверь пожалуйста номер закупки. Для 44ФЗ — 19 цифр, для 223ФЗ — 11 цифр!")
            return
        await pg.execute("INSERT INTO inbox(telegram_id, task_type, zakupka_number) VALUES($1,$2,$3)",
                         msg.from_user.id, 'zakupka', text)
        await rd.set(f"state:{msg.from_user.id}", "wait_1c")
        await msg.answer("Записал номер закупки, жду подтверждение из 1С.")

    elif state == "await_inn":
        if not valid_inn(text):
            await msg.answer("Проверь ИНН, должно быть 10 или 12 цифр!")
            return
        await pg.execute("INSERT INTO inbox(telegram_id, task_type, inn) VALUES($1,$2,$3)",
                         msg.from_user.id, 'inn_provided', text)
        await rd.set(f"state:{msg.from_user.id}", "wait_1c")
        await msg.answer("ИНН записан, жду ответ от 1С.")

    # другие ветви: confirm_use_company, choose_company, await_company_name, …

    await pg.close()
    await rd.close()

async def poll_outbox():
    pg = await asyncpg.connect(PG_DSN)
    while True:
        rows = await pg.fetch("SELECT id, telegram_id, message FROM outbox WHERE sent=false")
        for r in rows:
            await bot.send_message(r['telegram_id'], r['message'])
            await pg.execute("UPDATE outbox SET sent=true WHERE id=$1", r['id'])
        await asyncio.sleep(10)

async def main():
    asyncio.create_task(poll_outbox())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
