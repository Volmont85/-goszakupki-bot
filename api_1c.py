# api_1c.py

import os
import secrets
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text
from datetime import datetime
from database import SessionLocal
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
    print("📥 Получено:", data)

    try:
        async with SessionLocal() as session:
            await session.execute(text("""
                UPDATE inbox
                   SET message = :msg,
                       zakupka_number = :zn,
                       updated_at = NOW(),
                       status = :st
                 WHERE id = :id
            """), {
                "id": int(data.get("id")),  # <- приведи к int
                "msg": data.get("message"),
                "zn": data.get("zakupka_number"),
                "st": data.get("status", "done")
            })
            await session.commit()

            res = await session.execute(
                text("SELECT telegram_id FROM inbox WHERE id=:id"),
                {"id": int(data["id"])}
            )
            row = res.fetchone()

        if row:
            tg = row[0]
            txt = f"⚙️ Статус: {data.get('message')}"
            try:
                await bot.send_message(tg, txt)
            except Exception as e:
                print("Ошибка при отправке в Telegram:", e)
        return {"ok": True}

    except Exception as e:
        print("❌ Ошибка в api_result:", e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
