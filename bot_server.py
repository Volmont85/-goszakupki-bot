# ================================================================
# IMPORTS
# ================================================================
import os
import re
import aiohttp
import asyncio
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

from database import engine
from models import Base

# ================================================================
# INITIAL SETUP
# ================================================================
async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ================================================================
# ENV
# ================================================================
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_DSN = os.getenv("POSTGRES_DSN")
API_KEY = os.getenv("API_KEY")
PORT = int(os.environ.get("PORT", 443))

# ================================================================
# FASTAPI APP
# ================================================================
app = FastAPI(title="Telegram ↔ 1C Integration")

# ================================================================
# TELEGRAM BOT
# ================================================================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================================================================
# DATABASE
# ================================================================
engine = create_async_engine(DB_DSN, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
# HELPER: COMPANY NAME LOOKUP
# ================================================================
async def get_company_name_by_inn(inn: str) -> str | None:
    """Парсит list-org.com и возвращает чистое название компании."""
    try:
        url = f"https://www.list-org.com/search?type=inn&val={inn}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("div", class_="org_list")
        if link:
            href = link.find("a", href=True)
            if href:
                return href.get_text(strip=True)

        alt = soup.find("a", href=re.compile(r"^/company/\d+"))
        if alt:
            return alt.get_text(strip=True)
    except Exception as e:
        print(f"[WARN] list-org parse error: {e}")
    return None

# ================================================================
# COMMAND /start
# ================================================================
@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    await msg.answer("Привет! Пришли номер закупки (11 или 19 цифр):")
    await state.set_state(PurchaseStates.WAIT_ZAKUPKA)

# ================================================================
# HANDLER: WAIT_ZAKUPKA
# ================================================================
@dp.message(PurchaseStates.WAIT_ZAKUPKA)
async def handle_zakupka(msg: Message, state: FSMContext):
    num = msg.text.strip()
    if not validate_zakupka(num):
        await msg.answer("⚠️ Номер закупки должен содержать 11 или 19 цифр.")
        return

    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                INSERT INTO inbox (telegram_id, zakupka_num)
                VALUES (:tg, :num)
                RETURNING id
            """),
            {"tg": msg.from_user.id, "num": num},
        )
        new_id = result.scalar_one()
        await session.commit()

    await state.update_data(zakupka=num, zakupka_id=new_id)

    async with SessionLocal() as session:
        res = await session.execute(
            text("SELECT inn, company_name FROM TelegramID WHERE telegram_id=:tg"),
            {"tg": msg.from_user.id},
        )
        rows = res.fetchall()

    if not rows:
        await msg.answer("Теперь пришли ИНН компании:")
        await state.set_state(PurchaseStates.WAIT_INN)
    elif len(rows) == 1:
        inn, name = rows[0]
        await msg.answer(f"Участвуем от «{name}» (ИНН {inn}), верно?")
        await state.update_data(inn=inn, company_name=name)
        await state.set_state(PurchaseStates.CONFIRM_ONE)
    else:
        text_list = "\n".join(f"{i+1}. {r[1]} (ИНН {r[0]})" for i, r in enumerate(rows))
        await msg.answer(
            f"Найдено несколько фирм:\n{text_list}\n\n"
            "Введи номер нужной фирмы либо ИНН."
        )
        await state.update_data(companies=rows)
        await state.set_state(PurchaseStates.CHOOSE_COMPANY)

# ================================================================
# HANDLER: WAIT_INN
# ================================================================
@dp.message(PurchaseStates.WAIT_INN)
async def handle_inn(msg: Message, state: FSMContext):
    inn = msg.text.strip()
    if not validate_inn(inn):
        await msg.answer("⚠️ Проверь ИНН (10 или 12 цифр).")
        return

    company = await get_company_name_by_inn(inn)
    data = await state.get_data()

    async with SessionLocal() as session:
        res = await session.execute(
            text("""SELECT 1 FROM TelegramID WHERE telegram_id=:tg AND inn=:inn"""),
            {"tg": msg.from_user.id, "inn": inn},
        )
        exists = res.scalar()

        if not exists:
            await session.execute(
                text("""
                    INSERT INTO TelegramID (telegram_id, inn, company_name, username, first_name, last_name)
                    VALUES (:tg, :inn, :name, :username, :first_name, :last_name)
                """),
                {
                    "tg": msg.from_user.id,
                    "inn": inn,
                    "name": company or "—",
                    "username": msg.from_user.username,
                    "first_name": msg.from_user.first_name,
                    "last_name": msg.from_user.last_name,
                },
            )

        await session.execute(
            text("""
                UPDATE inbox
                SET inn=:inn, company_name=:nm
                WHERE telegram_id=:tg AND zakupka_num=:znum
            """),
            {
                "inn": inn,
                "nm": company or "—",
                "tg": msg.from_user.id,
                "znum": data.get("zakupka"),
            },
        )
        await session.commit()

    if company:
        await msg.answer(f"✅ Найдена компания: {company}")
    else:
        await msg.answer("⚠️ Не удалось найти компанию. Пришли название вручную:")
        await state.update_data(inn=inn)
        await state.set_state(PurchaseStates.WAIT_NAME)
        return

    await msg.answer("✅ Заявка сохранена и передана в 1С.\n/start — новая закупка")
    await state.clear()

# ================================================================
# HANDLER: WAIT_NAME
# ================================================================
@dp.message(PurchaseStates.WAIT_NAME)
async def handle_company_name(msg: Message, state: FSMContext):
    company_name = msg.text.strip()
    data = await state.get_data()
    inn = data.get("inn")

    async with SessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO TelegramID (telegram_id, inn, company_name, username, first_name, last_name)
                VALUES (:tg, :inn, :name, :username, :first_name, :last_name)
            """),
            {
                "tg": msg.from_user.id,
                "inn": inn,
                "name": company_name,
                "username": msg.from_user.username,
                "first_name": msg.from_user.first_name,
                "last_name": msg.from_user.last_name,
            },
        )
        await session.execute(
            text("""
                UPDATE inbox
                SET inn=:inn, company_name=:nm
                WHERE telegram_id=:tg AND zakupka_num=:znum AND id=:zakupka_id
            """),
            {
                "inn": inn,
                "nm": company_name,
                "tg": msg.from_user.id,
                "znum": data["zakupka"],
                "zakupka_id": data["zakupka_id"],
            },
        )
        await session.commit()

    await msg.answer(
        f"✅ Компания {company_name} сохранена для ИНН {inn}.\n"
        "Данные переданы в 1С.\n/start — новая закупка"
    )
    await state.clear()

# ================================================================
# HANDLER: CONFIRM_ONE
# ================================================================
@dp.message(PurchaseStates.CONFIRM_ONE)
async def confirm_one(msg: Message, state: FSMContext):
    if msg.text.lower() in {"да", "ага"}:
        data = await state.get_data()
        async with SessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE inbox
                    SET inn=:inn, company_name=:nm
                    WHERE telegram_id=:tg AND zakupka_num=:znum AND id=:zakupka_id
                """),
                {
                    "inn": data["inn"],
                    "nm": data["company_name"],
                    "tg": msg.from_user.id,
                    "znum": data["zakupka"],
                    "zakupka_id": data["zakupka_id"],
                },
            )
            await session.commit()
        await msg.answer("✅ Заявка сохранена и передана в 1С.\n/start — новая закупка")
        await state.clear()
    else:
        await msg.answer("Пришли ИНН компании заново:")
        await state.set_state(PurchaseStates.WAIT_INN)

# ================================================================
# HANDLER: CHOOSE_COMPANY
# ================================================================
@dp.message(PurchaseStates.CHOOSE_COMPANY)
async def choose_company(msg: Message, state: FSMContext):
    data = await state.get_data()
    text_inp = msg.text.strip()

    inn, name = None, None
    if text_inp.isdigit() and len(text_inp) in (10, 12):
        inn = text_inp
        async with SessionLocal() as session:
            res = await session.execute(text("SELECT company_name FROM TelegramID WHERE inn=:i"), {"i": inn})
            row = res.fetchone()
        if row:
            name = row[0]
    elif text_inp.isdigit():
        idx = int(text_inp) - 1
        if 0 <= idx < len(data["companies"]):
            inn, name = data["companies"][idx]

    if not inn or not name:
        await msg.answer("⚠️ Неверный ввод. Введи номер фирмы или ИНН.")
        return

    async with SessionLocal() as session:
        res = await session.execute(
            text("SELECT 1 FROM inbox WHERE inn=:inn AND zakupka_num=:znum"),
            {"inn": inn, "znum": data["zakupka"]},
        )
        if res.scalar():
            await state.update_data(inn=inn, company_name=name)
            await msg.answer("⚠️ Такая закупка уже есть. Удалить?")
            await state.set_state(PurchaseStates.CONFIRM_DELETE)
            return

        await session.execute(
            text("""
                UPDATE inbox
                SET inn=:inn, company_name=:nm
                WHERE telegram_id=:tg AND zakupka_num=:znum
            """),
            {
                "inn": inn,
                "nm": name,
                "tg": msg.from_user.id,
                "znum": data["zakupka"],
            },
        )
        await session.commit()

    await msg.answer("✅ Заявка сохранена и передана в 1С.\n/start — новая закупка")
    await state.clear()

# ================================================================
# HANDLER: CONFIRM_DELETE
# ================================================================
@dp.message(PurchaseStates.CONFIRM_DELETE)
async def confirm_delete(msg: Message, state: FSMContext):
    data = await state.get_data()
    if msg.text.lower().strip() in {"да", "ага", "удали", "удалить"}:
        async with SessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE inbox
                    SET message='отказались', status='new'
                    WHERE inn=:inn AND zakupka_num=:znum
                """),
                {"inn": data["inn"], "znum": data["zakupka"]},
            )
            await session.commit()
        await msg.answer("✅ Закупка помечена как 'отказались'.\n/start — новая закупка")
    else:
        await msg.answer("Ок, ничего не меняю.\n/start — новая закупка")
    await state.clear()

# ================================================================
# CLEANUP TASKS
# ================================================================
async def cleanup_old_records_loop():
    while True:
        try:
            async with SessionLocal() as session:
                await session.execute(
                    text("DELETE FROM inbox WHERE created_at < :dt"),
                    {"dt": datetime.utcnow() - timedelta(days=60)},
                )
                await session.commit()
                print("[cleanup] Old records removed")
        except Exception as e:
            print(f"[cleanup] Error: {e}")
        await asyncio.sleep(86400)

async def cleanup_null_records_loop():
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(hours=48)
            async with SessionLocal() as session:
                result = await session.execute(
                    text("""
                        DELETE FROM inbox
                        WHERE (inn IS NULL OR TRIM(inn) = '')
                          AND created_at < :dt
                    """),
                    {"dt": cutoff},
                )
                await session.commit()
                print(f"[cleanup] Null cleanup: {result.rowcount} rows removed")
        except Exception as e:
            print(f"[cleanup] Null cleanup error: {e}")
        await asyncio.sleep(86400)

# ================================================================
# FASTAPI STARTUP
# ================================================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_old_records_loop())
    asyncio.create_task(cleanup_null_records_loop())
    asyncio.create_task(dp.start_polling(bot))
    print("[startup] Bot polling + cleanup started")

# ================================================================
# 1C ROUTERS
# ================================================================
from api_1c import router as api_1c_router
import odata

app.include_router(api_1c_router)
app.include_router(odata.router)

# ================================================================
# ENTRYPOINT
# ================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot_server:app", host="0.0.0.0", port=PORT, reload=False)
