# api_1c.py

import os
import re
import secrets
import json
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import text, bindparam
from database import SessionLocal
from models import Inbox
from bot_instance import bot  # импорт экземпляра бота

router = APIRouter()

API_KEY = os.getenv("API_KEY")
MainTg = os.getenv("MainTg")

# -------------------------------
# Проверка API‑ключа
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
            res = await session.execute(
                text("""
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
                """)
            )
            data = [dict(r._mapping) for r in res.fetchall()]

        # 2️⃣ Меняем их статус на "in_process"
        if data:
            ids = [r["id"] for r in data]  # список ID‑шников

            async with SessionLocal() as session:
                query = text("""
                    UPDATE inbox
                       SET status = 'in_process',
                           updated_at = :now
                     WHERE id IN :ids
                """).bindparams(bindparam("ids", expanding=True))

                await session.execute(
                    query,
                    {"now": datetime.utcnow(), "ids": ids},
                )
                await session.commit()

        # 3️⃣ Возвращаем обновлённые закупки
        return data

    except Exception as e:
        return {"ok": False, "message": str(e)}

# -------------------------------
# POST /api/1c/deadlines
# 1С шлёт список активных закупок (заявка не подана, дедлайн в будущем).
# id1c — номер документа 1С, уникальный ключ.
# -------------------------------
@router.post("/api/1c/deadlines")
async def receive_deadlines(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    payload = await request.json()

    if not isinstance(payload, list):
        return {"ok": False, "message": "Expected a JSON array"}

    saved = 0
    try:
        async with SessionLocal() as session:
            for item in payload:
                id1c = str(item.get("id1c") or "").strip()
                if not id1c:
                    continue
                try:
                    # 1С шлёт дату через БезопасноеПреобразованиеJSON (ISO 'yyyy-MM-ddTHH:mm:ss')
                    deadline = datetime.fromisoformat(str(item.get("deadline")))
                except Exception:
                    continue

                submitted = bool(item.get("submitted"))

                await session.execute(
                    text("""
                        INSERT INTO zakupka_deadlines (id1c, zakupka_num, zakazchik, deadline, submitted, updated_at)
                        VALUES (:id1c, :num, :zak, :dl, :sub, :now)
                        ON CONFLICT (id1c) DO UPDATE
                        SET zakupka_num = EXCLUDED.zakupka_num,
                            zakazchik = EXCLUDED.zakazchik,
                            deadline = EXCLUDED.deadline,
                            submitted = zakupka_deadlines.submitted OR EXCLUDED.submitted,
                            updated_at = EXCLUDED.updated_at
                    """),
                    {
                        "id1c": id1c,
                        "num": item.get("zakupka_num") or "",
                        "zak": item.get("zakazchik") or "",
                        "dl": deadline,
                        "sub": submitted,
                        "now": datetime.utcnow(),
                    },
                )
                # submitted объединяем как "true побеждает" (см. SQL выше) - 1С теперь шлёт
                # ВСЕ активные закупки (не только неподанные), это защищает от гонки, если
                # заявку подтвердили кнопкой в боте, а 1С ещё не успела подтянуть это обратно
                # через ИмпортПодтвержденийЗаявкаПодана.
                saved += 1
            await session.commit()

        return {"ok": True, "count": saved}

    except Exception as e:
        return {"ok": False, "message": str(e)}

# -------------------------------
# GET /api/1c/zayavka-confirmations
# 1С забирает список неподтверждённых ей записей (confirmed_at не пусто, acked=false)
# -------------------------------
@router.get("/api/1c/zayavka-confirmations")
async def get_confirmations(api_key: str = Header(None)):
    await check_token(api_key)

    try:
        async with SessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT id1c, zakupka_num
                      FROM zakupka_deadlines
                     WHERE confirmed_at IS NOT NULL
                       AND acked = false
                """)
            )
            return [dict(r._mapping) for r in res.fetchall()]

    except Exception as e:
        return {"ok": False, "message": str(e)}

# -------------------------------
# POST /api/1c/zayavka-confirmations/done
# 1С подтверждает, что проставила ЗаявкаПодана=Истина по этим id1c — ставим acked=true.
# -------------------------------
@router.post("/api/1c/zayavka-confirmations/done")
async def ack_confirmations(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    payload = await request.json()

    if not isinstance(payload, list) or not payload:
        return {"ok": True, "acked": 0}

    try:
        async with SessionLocal() as session:
            query = text("""
                UPDATE zakupka_deadlines
                   SET acked = true
                 WHERE id1c IN :ids
            """).bindparams(bindparam("ids", expanding=True))

            res = await session.execute(query, {"ids": [str(x) for x in payload]})
            await session.commit()

        return {"ok": True, "acked": res.rowcount}

    except Exception as e:
        return {"ok": False, "message": str(e)}

# -------------------------------
# Вспомогательная функция
# -------------------------------
def markdown_link_to_html(text: str) -> str:
    """Преобразует Markdown‑ссылку [text](url) в HTML"""
    if not isinstance(text, str) or not text.strip():
        return ""
    pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    return re.sub(pattern, r'<a href="\2">\1</a>', text)

# -------------------------------
# POST /api/result
# -------------------------------
@router.post("/api/result")
async def api_result(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    data = await request.json()

    # ✳️ Извлекаем значения
    rec_id = int(data.get("id")) if data.get("id") else None
    message = (data.get("message") or "").strip().lower()
    status = (data.get("status") or "").strip()      # ожидаем 'done' или 'delete'
    zakupka_number = data.get("zakupka_number") or ""
    zakupka_number_html = markdown_link_to_html(zakupka_number)

    if rec_id is None:
        return {"ok": False, "message": "Missing id"}

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
                    "now": datetime.utcnow(),
                },
            )
            await session.commit()

            # 📩 Получаем telegram_id после обновления
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
        elif "уже создана" in message:
            txt = f"⚠️ Статус обновлён — {zakupka_number_html}"
        else:
            txt = f"ℹ️ Статус обновлён: {message}\n{zakupka_number_html}"

        recipients = {int(tg), int(MainTg)}  # множество исключает дубли
        for chat_id in recipients:
            await bot.send_message(chat_id, txt, parse_mode="HTML")
            await bot.send_message(chat_id, "Для добавления новой закупки нажми /start")


        return {"ok": True, "message": f"Record {rec_id} updated to status '{status}'"}

    except Exception as e:
        return {"ok": False, "message": str(e)}
