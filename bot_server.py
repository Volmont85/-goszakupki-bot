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
@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    await msg.answer("Привет! Пришли номер закупки для участия (11 или 19 цифр):")
    await state.set_state(PurchaseStates.WAIT_ZAKUPKA)

@dp.message(PurchaseStates.WAIT_ZAKUPKA)
async def handle_zakupka(msg: Message, state: FSMContext):
    num = msg.text.strip()
    if not validate_zakupka(num):
        await msg.answer("Проверь пожалуйста номер закупки. Для 44ФЗ должно быть 19 цифр, для 223ФЗ — 11.")
        return
    async with SessionLocal() as session:
        await session.execute(text(
            "INSERT INTO inbox (telegram_id, zakupka_num) VALUES (:tg, :num)"
        ), {"tg": msg.from_user.id, "num": num})
        await session.commit()
    await state.update_data(zakupka=num)
    # Проверяем, есть ли компания
    async with SessionLocal() as session:
        res = await session.execute(text(
            "SELECT inn, company_name FROM TelegramID WHERE telegram_id=:tg"
        ), {"tg": msg.from_user.id})
        rows = res.fetchall()
    if not rows:
        await msg.answer("Пришли ИНН компании, от которой планируем участие:")
        await state.set_state(PurchaseStates.WAIT_INN)
    elif len(rows) == 1:
        inn, name = rows[0]
        await msg.answer(f"Участвуем от «{name}» (ИНН {inn}), да?")
        await state.update_data(inn=inn, company_name=name)
        await state.set_state(PurchaseStates.CONFIRM_ONE)
    else:
        text_list = "\n".join([f"{i+1}. {r[1]} (ИНН {r[0]})" for i, r in enumerate(rows)])
        await msg.answer(f"Для тебя я нашёл следующие фирмы:\n{text_list}\n\nВведи номер нужной фирмы или пришли новый ИНН:")
        await state.update_data(companies=rows)
        await state.set_state(PurchaseStates.CHOOSE_COMPANY)

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
                await msg.answer("Такая компания не найдена, напиши её название для добавления:")
                await state.update_data(inn=inn)
                await state.set_state(PurchaseStates.WAIT_NAME)
                return
            name = row[0]
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

@dp.message(PurchaseStates.WAIT_INN)
async def handle_inn(msg: Message, state: FSMContext):
    inn = msg.text.strip()
    if not validate_inn(inn):
        await msg.answer("Проверь ИНН, должно быть 10 или 12 цифр!")
        return
    async with SessionLocal() as session:
        res = await session.execute(text("SELECT company_name FROM TelegramID WHERE inn=:inn"), {"inn": inn})
        row = res.fetchone()
    if not row:
        await msg.answer("Такая компания не найдена. Напиши название компании в соответствии с выпиской ЕГРЮЛ:")
        await state.update_data(inn=inn)
        await state.set_state(PurchaseStates.WAIT_NAME)
    else:
        name = row[0]
        await state.update_data(inn=inn, company_name=name)
        await msg.answer(f"Участвуем от «{name}», да?")
        await state.set_state(PurchaseStates.CONFIRM_ONE)

@dp.message(PurchaseStates.WAIT_NAME)
async def handle_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    data = await state.get_data()
    async with SessionLocal() as session:
        await session.execute(text("""
            INSERT INTO TelegramID (telegram_id, inn, company_name) VALUES (:tg, :inn, :nm)
        """), {"tg": msg.from_user.id, "inn": data["inn"], "nm": name})
        await session.execute(text("""
            UPDATE inbox SET inn=:inn, company_name=:nm WHERE telegram_id=:tg AND zakupka_num=:znum
        """), {"inn": data["inn"], "nm": name, "tg": msg.from_user.id, "znum": data["zakupka"]})
        await session.commit()
    await msg.answer(f"✅ Компания {name} добавлена. Заявка передана на обработку в 1С.")
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
# Run
# ------------------------------#
def main():
    import uvicorn
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot))
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

if __name__ == "__main__":
    main()
