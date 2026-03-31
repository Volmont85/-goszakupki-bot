# api_1c.py

import os
import secrets
import json
import re
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text
from datetime import datetime
from database import SessionLocal
from models import Inbox
from bot_instance import bot   # см. ниже

router = APIRouter()

API_KEY = os.getenv("API_KEY")
MainTg = os.getenv("MainTg")


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
            # 1️⃣ Получаем закупки со статусом "new"
            res = await session.execute(text("""
                SELECT
                    id,
                    telegram_id,
                    inn,
                    company_name,
                    zakupka_num,
                    message,
                    NULL AS zakupka_number
                FROM inbox
                WHERE status = 'new'
                  AND inn IS NOT NULL
            """))
            data = [dict(r._mapping) for r in res.fetchall()]

            # 2️⃣ Меняем их статус на "in_process"
            if data:
                ids = [str(d["id"]) for d in data]
                await session.execute(
                    text("""
                        UPDATE inbox
                        SET status = 'in_process',
                            updated_at = :now
                        WHERE id IN :ids
                    """),
                    {"now": datetime.utcnow().isoformat(), "ids": tuple(ids)}
                )
                await session.commit()

        # 3️⃣ Возвращаем закупки (уже помеченные "in_process" в базе)
        return data

    except Exception as e:
        return {"error": str(e)}


# -------------------------------
# POST /api/result
# -------------------------------

def markdown_link_to_html(text: str) -> str:
    """Преобразует Markdown-ссылку [text](url) в HTML"""
    if not isinstance(text, str) or not text.strip():
        return ""
    pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    return re.sub(pattern, r'<a href="\2">\1</a>', text)


@router.post("/api/result")
async def api_result(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    data = await request.json()

    # ✳️ Извлекаем значения
    rec_id = int(data.get("id")) if data.get("id") else None
    message = (data.get("message") or "").strip().lower()
    status = (data.get("status") or "").strip()     # ожидаем 'done' или 'delete'
    zakupka_number = data.get("zakupka_number") or ""
    zakupka_number_html = markdown_link_to_html(zakupka_number)

    if rec_id is None:
        return {"error": "Missing id"}

    try:
        async with SessionLocal() as session:
            # 🔧 обновляем запись в inbox, только если она ещё в in_process
            await session.execute(
                text("""
                    UPDATE inbox
                       SET message = :msg,
                           zakupka_number = :zn,
                           updated_at = :now,
                           status = :st
                     WHERE id = :id
                       AND status = 'in_process'
                """),
                {
                    "id": rec_id,
                    "msg": message,
                    "zn": zakupka_number_html,
                    "st": status,
                    "now": datetime.utcnow().isoformat()
                }
            )
            await session.commit()

            # 📩 Получаем telegram_id
            res = await session.execute(
                text("SELECT telegram_id FROM inbox WHERE id = :id"),
                {"id": rec_id},
            )
            row = res.fetchone()

        if not row or not row[0]:
            return {"ok": True, "message": "Record updated, but no Telegram ID found"}

        tg = row[0]

        # 📨 Формируем текст уведомления
        if "удален" in message or status == "delete":
            txt = f"❌ Закупка удалена в 1С.\n{zakupka_number_html}"
        elif "добавлен" in message or status == "done":
            txt = f"✅ Закупка добавлена\n{zakupka_number_html}"
        elif "уже создан" in message:
            txt = f"⚠️ Статус обновлён — {zakupka_number_html}"
        else:
            txt = f"ℹ️ Статус обновлён: {message}\n{zakupka_number_html}"

        await bot.send_message(tg, txt, parse_mode="HTML")
        await bot.send_message(tg, "Для добавления новой закупки нажми /start")

        # 👀 Уведомляем также администратора
        if tg != MainTg:
            await bot.send_message(MainTg, txt, parse_mode="HTML")
            await bot.send_message(MainTg, "Для добавления новой закупки нажми /start")

        return {"ok": True, "message": f"Record {rec_id} updated to status '{status}'"}

    except Exception as e:
        # Ловим и возвращаем текст ошибки
        return {"error": str(e)}
