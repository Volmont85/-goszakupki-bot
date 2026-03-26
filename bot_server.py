# ================================================================
# IMPORTS
# ================================================================
import os
import re
import aiohttp
import asyncio
import secrets
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

# ================================================================
# ENV
# ================================================================
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_DSN = os.getenv("POSTGRES_DSN")
API_KEY = os.getenv("API_KEY") or secrets.token_urlsafe(32)

# ================================================================
# FASTAPI APP
# ================================================================
app = FastAPI(title="Telegram ↔ 1C Integration")

# ================================================================
# TELEGRAM
# ================================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================================================================
# DATABASE
# ================================================================
engine = create_async_engine(DB_DSN, echo=False, pool_pre_ping=True)

SessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# ================================================================
# FSM STATES
# ================================================================
class PurchaseStates(StatesGroup):
    WAIT_ZAKUPKA = State()
    WAIT_INN = State()
    WAIT_NAME = State()
    CHOOSE_COMPANY = State()
    CONFIRM_ONE = State()
    CONFIRM_DELETE = State()

# ================================================================
# VALIDATORS
# ================================================================
def validate_zakupka(num: str) -> bool:
    return num.isdigit() and len(num) in (11, 19)

def validate_inn(num: str) -> bool:
    return num.isdigit() and len(num) in (10, 12)

# ================================================================
# PARSING list-org
# ================================================================
async def get_company_name_by_inn(inn: str) -> str | None:
    try:
        url = f"https://www.list-org.com/search?type=inn&val={inn}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")

        org_div = soup.find("div", class_="org_list")
        if org_div:
            link = org_div.find("a", href=True)
            if link:
                return link.get_text(strip=True)

        link = soup.find("a", href=re.compile(r"^/company/\d+"))
        if link:
            return link.get_text(strip=True)

    except Exception as e:
        print(f"[WARN] list-org parsing error: {e}")

    return None

# ================================================================
# TELEGRAM HANDLERS
# ================================================================
@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    await msg.answer("Привет! Пришли номер закупки (11 или 19 цифр):")
    await state.set_state(PurchaseStates.WAIT_ZAKUPKA)


@dp.message(PurchaseStates.WAIT_ZAKUPKA)
async def handle_zakupka(msg: Message, state: FSMContext):
    num = msg.text.strip()

    if not validate_zakupka(num):
        await msg.answer("Проверь номер закупки. 44‑ФЗ — 19 цифр, 223‑ФЗ — 11.")
        return

    async with SessionLocal() as session:
        await session.execute(
            text("INSERT INTO inbox (telegram_id, zakupka_num) VALUES (:tg, :num)"),
            {"tg": msg.from_user.id, "num": num},
        )
        await session.commit()

    await state.update_data(zakupka=num)

    async with SessionLocal() as session:
        res = await session.execute(
            text("SELECT inn, company_name FROM TelegramID WHERE telegram_id=:tg"),
            {"tg": msg.from_user.id},
        )
        rows = res.fetchall()

    if not rows:
        await msg.answer("Пришли ИНН компании:")
        await state.set_state(PurchaseStates.WAIT_INN)
        return

    if len(rows) == 1:
        inn, name = rows[0]
        await msg.answer(f"Участвуем от «{name}» (ИНН {inn})?")
        await state.update_data(inn=inn, company_name=name)
        await state.set_state(PurchaseStates.CONFIRM_ONE)
        return

    text_list = "\n".join(
        [f"{i+1}. {r[1]} (ИНН {r[0]})" for i, r in enumerate(rows)]
    )

    await msg.answer(
        f"Нашёл несколько фирм:\n{text_list}\n\nВведи номер или новый ИНН:"
    )

    await state.update_data(companies=rows)
    await state.set_state(PurchaseStates.CHOOSE_COMPANY)

# ================================================================
# CLEANUP TASK
# ================================================================
async def cleanup_old_records_loop():
    while True:
        try:
            async with SessionLocal() as session:
                await session.execute(
                    text("DELETE FROM inbox WHERE created_at < :dt"),
                    {"dt": datetime.utcnow() - timedelta(days=60)}
                )
                await session.commit()
                print("[cleanup] Old records removed")
        except Exception as e:
            print(f"[cleanup] Error: {e}")

        await asyncio.sleep(86400)

# ================================================================
# FASTAPI STARTUP
# ================================================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_old_records_loop())
    asyncio.create_task(dp.start_polling(bot))
    print("[startup] Bot polling + cleanup started")

# ================================================================
# 1C ROUTER
# ================================================================
from api_1c import router as api_1c_router
app.include_router(api_1c_router)

# ================================================================
# ENTRYPOINT
# ================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot_server:app", host="0.0.0.0", port=8000, reload=False)
