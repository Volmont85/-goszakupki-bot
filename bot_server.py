import asyncio
import os
import re
import secrets
from datetime import datetime
from fastapi import FastAPI, Request, Header, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# ------------------------------#
# ENV and setup
# ------------------------------#
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_DSN = os.getenv("POSTGRES_DSN")
API_KEY = os.getenv("API_KEY") or secrets.token_urlsafe(15)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI(title="Telegram ↔ 1C Integration")

# ------------------------------#
# DB setup
# ------------------------------#
engine = create_async_engine(DB_DSN, echo=False)
SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# ------------------------------#
# FSM States
# ------------------------------#
class PurchaseStates(StatesGroup):
    WAIT_ZAKUPKA = State()
    WAIT_INN = State()
    WAIT_NAME = State()
    CHOOSE_COMPANY = State()
    CONFIRM_ONE = State()
    CONFIRM_AUTO = State()

# ------------------------------#
# Helpers
# ------------------------------#
def validate_zakupka(num: str) -> bool:
    return num.isdigit() and len(num) in (11, 19)

def validate_inn(num: str) -> bool:
    return num.isdigit() and len(num) in (10, 12)

# ------------------------------#
# Telegram logic
# ------------------------------#
import aiohttp
from bs4 import BeautifulSoup

# --- поиск компании по ИНН через list-org.com ---
async def get_company_name_by_inn(inn: str) -> str | None:
    """
    Возвращает название компании с сайта list-org.com по ИНН,
    очищая текст от HTML-тегов (<b>, <i> и др.).
    """
    try:
        url = f"https://www.list-org.com/search?type=inn&val={inn}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                html = await response.text()

        soup = BeautifulSoup(html, "html.parser")

        # Основной вариант
        org_div = soup.find("div", class_="org_list")
        if org_div:
            link = org_div.find("a", href=True)
            if link:
                # Получаем текст без тегов "<b>" и любых других вложенных
                company_name = link.get_text(strip=True)
                return company_name

        # Резерв: если структура другая
        link = soup.find("a", href=re.compile(r"^/company/\d+"))
        if link:
            company_name = link.get_text(strip=True)
            return company_name

    except Exception as e:
        print(f"[WARN] Ошибка при парсинге list-org: {e}")

    return None




@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    await msg.answer("Привет! Пришли номер закупки для участия (11 или 19 цифр):")
    await state.set_state(PurchaseStates.WAIT_ZAKUPKA)


# --- этап 1: получаем номер закупки ---
@dp.message(PurchaseStates.WAIT_ZAKUPKA)
async def handle_zakupka(msg: Message, state: FSMContext):
    num = msg.text.strip()
    if not validate_zakupka(num):
        await msg.answer("Проверь номер закупки. Для 44‑ФЗ — 19 цифр, для 223‑ФЗ — 11.")
        return

    async with SessionLocal() as session:
        await session.execute(
            text("INSERT INTO inbox (telegram_id, zakupka_num) VALUES (:tg, :num)"),
            {"tg": msg.from_user.id, "num": num},
        )
        await session.commit()

    await state.update_data(zakupka=num)

    # Проверяем, есть ли связанная компания
    async with SessionLocal() as session:
        res = await session.execute(
            text("SELECT inn, company_name FROM TelegramID WHERE telegram_id=:tg"),
            {"tg": msg.from_user.id},
        )
        rows = res.fetchall()

    if not rows:
        await msg.answer("Теперь пришли ИНН компании, от которой планируем участие:")
        await state.set_state(PurchaseStates.WAIT_INN)
    elif len(rows) == 1:
        inn, name = rows[0]
        await msg.answer(f"Участвуем от «{name}» (ИНН {inn}), да?")
        await state.update_data(inn=inn, company_name=name)
        await state.set_state(PurchaseStates.CONFIRM_ONE)
    else:
        text_list = "\n".join(
            [f"{i+1}. {r[1]} (ИНН {r[0]})" for i, r in enumerate(rows)]
        )
        await msg.answer(
            f"Для тебя я нашёл несколько фирм:\n{text_list}"
            "\n\nВведи номер нужной фирмы или пришли новый ИНН:"
        )
        await state.update_data(companies=rows)
        await state.set_state(PurchaseStates.CHOOSE_COMPANY)


# --- этап 2: пользователь присылает ИНН, ищем компанию в list‑org ---
@dp.message(PurchaseStates.WAIT_INN)
async def handle_inn(msg: Message, state: FSMContext):
    inn = msg.text.strip()
    if not validate_inn(inn):
        await msg.answer("Проверь ИНН — должно быть 10 или 12 цифр!")
        return

    company = await get_company_name_by_inn(inn)

    async with SessionLocal() as session:
        if company:
            # нашли сразу — сохраняем
            await session.execute(
                text(
                    "INSERT INTO TelegramID (telegram_id, inn, company_name) "
                    "VALUES (:tg, :inn, :name)"
                ),
                {"tg": msg.from_user.id, "inn": inn, "name": company},
            )
            await session.commit()

            await msg.answer(
                f"✅ ИНН {inn} принадлежит компании:\n{company}\n"
                "Записал, продолжаем."
            )
            await state.update_data(inn=inn, company_name=company)
            await state.set_state(PurchaseStates.CONFIRM_AUTO)
            data = await state.get_data()
            await confirm_auto(msg, state, data)
        else:
            # не нашли — просим название вручную
            await msg.answer(
                "❗ Не удалось найти компанию по ИНН.\n"
                "Пришли полное название компании (как в ЕГРЮЛ):"
            )
            await state.update_data(inn=inn)
            await state.set_state(PurchaseStates.WAIT_NAME)


# --- этап 3: если не нашли по ИНН, пользователь вводит название сам ---
@dp.message(PurchaseStates.WAIT_NAME)
async def handle_company_name(msg: Message, state: FSMContext):
    data = await state.get_data()
    inn = data.get("inn")
    company_name = msg.text.strip()

    async with SessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO TelegramID (telegram_id, inn, company_name) "
                "VALUES (:tg, :inn, :name)"
            ),
            {"tg": msg.from_user.id, "inn": inn, "name": company_name},
        )
        await session.commit()

    await msg.answer(
        f"✅ Компания {company_name} сохранена для ИНН {inn}.\n"
        "Теперь можно продолжать работу."
    )
    await state.update_data(company_name=company_name)
    await state.set_state(PurchaseStates.CONFIRM_AUTO)
    data = await state.get_data()
    await confirm_auto(msg, state, data)

@dp.message(PurchaseStates.CONFIRM_ONE)
async def confirm_one(msg: Message, state: FSMContext):
    answer = msg.text.lower()
    data = await state.get_data()
    if answer in ("да", "ага"):
        # фиксируем в inbox firm+inn
        async with SessionLocal() as session:
            await session.execute(text("""
                UPDATE inbox SET inn=:inn, company_name=:nm WHERE telegram_id=:tg AND zakupka_num=:znum
            """), {"inn": data["inn"], "nm": data["company_name"], "tg": msg.from_user.id, "znum": data["zakupka"]})
            await session.commit()
        await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.")
        await state.clear()
    else:
        await msg.answer("Пришли ИНН компании, от которой планируем участие:")
        await state.set_state(PurchaseStates.WAIT_INN)

@dp.message(PurchaseStates.CONFIRM_AUTO)
async def confirm_auto(msg: Message, state: FSMContext):
    async with SessionLocal() as session:
        await session.execute(text("""
            UPDATE inbox
            SET inn = :inn, company_name = :nm
            WHERE telegram_id = :tg AND zakupka_num = :znum
        """), {
            "inn": data["inn"],
            "nm": data["company_name"],
            "tg": msg.from_user.id,
            "znum": data["zakupka"]
        })
        await session.commit()

    await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.")
    await state.clear()
    

@dp.message(PurchaseStates.CHOOSE_COMPANY)
async def choose_company(msg: Message, state: FSMContext):
    data = await state.get_data()
    text_inp = msg.text.strip()
    if text_inp.isdigit():
        idx = int(text_inp)-1
        if idx < 0 or idx >= len(data["companies"]):
            await msg.answer("Ответ неверный, пожалуйста повтори номер нужной фирмы.")
            return
        inn, name = data["companies"][idx]
    elif validate_inn(text_inp):
        inn = text_inp
        async with SessionLocal() as session:
            res = await session.execute(text("SELECT company_name FROM TelegramID WHERE inn=:i"), {"i": inn})
            row = res.fetchone()
            if not row:
                await state.set_state(PurchaseStates.WAIT_INN)
    else:
        await msg.answer("Ответ неверный, введи номер фирмы или ИНН.")
        return

    async with SessionLocal() as session:
        await session.execute(text("""
            UPDATE inbox SET inn=:inn, company_name=:nm WHERE telegram_id=:tg AND zakupka_num=:znum
        """), {"inn": inn, "nm": name, "tg": msg.from_user.id, "znum": data["zakupka"]})
        await session.commit()
    await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.")
    await state.clear()

# ------------------------------#
# API endpoints for 1C
# ------------------------------#
async def check_token(api_key: str = Header(None)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/api/inbox")
async def api_inbox(api_key: str = Header(None)):
    await check_token(api_key)
    async with SessionLocal() as session:
        res = await session.execute(text("SELECT * FROM inbox WHERE status='new'"))
        data = [dict(r._mapping) for r in res.fetchall()]
    return data

@app.post("/api/result")
async def api_result(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    data = await request.json()
    async with SessionLocal() as session:
        await session.execute(text("""
            UPDATE inbox SET status=:st, message=:msg, updated_at=NOW() WHERE id=:id
        """), {"st": data.get("status"), "msg": data.get("message"), "id": data.get("id")})
        await session.commit()
    # уведомление в Telegram
    async with SessionLocal() as session:
        res = await session.execute(text("SELECT telegram_id FROM inbox WHERE id=:id"), {"id": data["id"]})
        row = res.fetchone()
    if row:
        tg = row[0]
        text_msg = "✅ Заявка обработана в 1С." if data.get("status") == "done" else "⚠️ Произошла ошибка при обработке заявки."
        await bot.send_message(tg, text_msg)
    return {"ok": True}

@app.post("/api/from1c/error")
async def api_error(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    payload = await request.json()
    async with SessionLocal() as session:
        await session.execute(text("""
            INSERT INTO inbox (telegram_id, message, status) VALUES (0, :msg, 'error')
        """), {"msg": payload.get("message")})
        await session.commit()
    return {"ok": True}

# ------------------------------#
# Start bot (современный способ)
# ------------------------------#
async def main():
    # Здесь можно добавить фоновые задачи, например FastAPI если нужно.
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Современный запуск без get_event_loop()
    asyncio.run(main())
