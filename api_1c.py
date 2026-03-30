# api_1c.py

import os
import secrets
import json
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text
from datetime import datetime
from database import SessionLocal
from models import Inbox
from bot_instance import bot   # см. ниже

router = APIRouter()

API_KEY = os.getenv("API_KEY")


# -------------------------------
# Проверка API ключа
# -------------------------------
async def check_token(api_key: str = Header(None)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# -------------------------------
# GET /api/inbox
# -------------------------------
@router.get("/api/inbox")
async def api_inbox(api_key: str = Header(None)):
    await check_token(api_key)

    try:
        async with SessionLocal() as session:
            res = await session.execute(text("""
                SELECT id, telegram_id, inn, company_name,
                       zakupka_num, message, zakupka_number
                FROM inbox
                WHERE status = 'new'
                  AND inn IS NOT NULL
            """))
            data = [dict(r._mapping) for r in res.fetchall()]
        return data
    except Exception as e:
        return {"error": str(e)}


# -------------------------------
# POST /api/result
# -------------------------------
@router.post("/api/result")
async def api_result(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    data = await request.json()

    # извлекаем значения
    id = int(data.get("id")) if data.get("id") else None
    message = data.get("message")
    zakupka_number = data.get("zakupka_number")
    status = data.get("status")

    # если id отсутствует — ошибка
    if id is None:
        return {"error": "Missing id"}

    async with SessionLocal() as session:
        # обновление в таблице inbox
        await session.execute(
            text("""
                UPDATE inbox
                   SET message = :msg,
                       zakupka_number = :zn,
                       updated_at = NOW(),
                       status = :st
                 WHERE id = :id
            """),
            {
                "id": id,
                "msg": message,
                "zn": zakupka_number,
                "st": status
            }
        )

        await session.commit()

        # получаем telegram_id
        res = await session.execute(
            text("SELECT telegram_id FROM inbox WHERE id=:id"),
            {"id": id}
        )
        row = res.fetchone()

    if row:
        tg = row[0]

        # формируем текст уведомления
        if message == "удалена":
            txt = "❌ Закупка удалена в 1С."
        elif message == "добавлена":
            txt = f"✅ Закупка добавлена в 1С:\n{zakupka_number}"
        else:
            txt = f"⚠️ Статус обновлён - {zakupka_number}"

        await bot.send_message(tg, txt)
        await bot.send_message(tg, "Для добавления новой закупки нажми /start")

    return {"ok": True}
