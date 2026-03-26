from bs4 import BeautifulSoup
import aiohttp
import asyncio
import os
import re
import secrets
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Header, HTTPException
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
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
USE_WEBHOOK = os.getenv("USE_WEBHOOK", "true").lower() == "true"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")


bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
app = FastAPI(title="Telegram ↔ 1C Integration")

# ------------------------------#
# DB setup
# ------------------------------#
engine = create_async_engine(DB_DSN, echo=False)
SessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False)

# ------------------------------#
# FSM States
# ------------------------------#


class PurchaseStates(StatesGroup):
    WAIT_ZAKUPKA = State()
    WAIT_INN = State()
    WAIT_NAME = State()
    CHOOSE_COMPANY = State()
    CONFIRM_ONE = State()
    CONFIRM_DELETE= State()


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
async def cmd_start(message: Message):
    await message.answer("✅ Бот работает!")
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
        await msg.answer("⚠️ Проверь ИНН — должно быть 10 или 12 цифр!")
        return

    company = await get_company_name_by_inn(inn)
    data = await state.get_data()

    if company:
        # нашли компанию — сохраняем
        async with SessionLocal() as session:
            # проверяем, есть ли уже запись в TelegramID
            res = await session.execute(
                text("""
                    SELECT 1 FROM TelegramID
                    WHERE telegram_id = :tg AND inn = :inn
                """),
                {"tg": msg.from_user.id, "inn": inn},
            )
            exists = res.scalar()

            if not exists:
                await session.execute(
                    text("""
                        INSERT INTO TelegramID (telegram_id, inn, company_name)
                        VALUES (:tg, :inn, :name)
                    """),
                    {"tg": msg.from_user.id, "inn": inn, "name": company},
                )

            # обновляем inbox
            await session.execute(
                text("""
                    UPDATE inbox
                    SET inn = :inn,
                        company_name = :nm
                    WHERE telegram_id = :tg
                      AND zakupka_num = :znum
                """),
                {
                    "inn": inn,
                    "nm": company,
                    "tg": msg.from_user.id,
                    "znum": data.get("zakupka"),
                },
            )
            await session.commit()

        await msg.answer(
            f"✅ ИНН {inn} принадлежит компании:\n{company}\n"
            "Записал, продолжаем."
        )
        await state.update_data(inn=inn, company_name=company)
        await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.\n"
                         "Для добавления новой закупки нажми /start")
        await state.clear()

    else:
        # не нашли — просим название вручную
        await msg.answer(
            "⚠️ Не удалось найти компанию по ИНН.\n"
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
    async with SessionLocal() as session:
        await session.execute(text("""
                UPDATE inbox SET inn=:inn, company_name=:nm WHERE telegram_id=:tg AND zakupka_num=:znum
            """), {"inn": data["inn"], "nm": data["company_name"], "tg": msg.from_user.id, "znum": data["zakupka"]})
    await session.commit()
    await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.\n"
                     "Для добавления новой закупки нажми /start")
    await state.clear()


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
        await msg.answer("✅ Заявка сохранена и передана на обработку в 1С.\n"
                         "Для добавления новой закупки нажми /start")
        await state.clear()
    else:
        await msg.answer("Пришли ИНН компании, от которой планируем участие:")
        await state.set_state(PurchaseStates.WAIT_INN)


@dp.message(PurchaseStates.CHOOSE_COMPANY)
async def choose_company(msg: Message, state: FSMContext):
    data = await state.get_data()
    text_inp = msg.text.strip()

    # --- определяем ИНН и название компании ---
    if text_inp.isdigit() and len(text_inp) in (10, 12):
        # если введён ИНН
        inn = text_inp
        async with SessionLocal() as session:
            res = await session.execute(
                text("SELECT company_name FROM TelegramID WHERE inn = :i"),
                {"i": inn},
            )
            row = res.fetchone()
        if row:
            name = row[0]
        else:
            await state.update_data(inn=inn)
            await state.set_state(PurchaseStates.WAIT_INN)
            #await msg.answer("⚠️ Не нашёл фирму с этим ИНН. Пришли правильный ИНН ещё раз.")
            return

    elif text_inp.isdigit():
        # если введено число — это индекс компании в списке
        idx = int(text_inp) - 1
        if idx < 0 or idx >= len(data["companies"]):
            await msg.answer("Ответ неверный, пожалуйста, повтори номер нужной фирмы.")
            return
        inn, name = data["companies"][idx]

    else:
        await msg.answer("⚠️ Ответ неверный, введи номер фирмы или ИНН.")
        return

    # --- Проверяем, добавлялась ли закупка ранее ---
    async with SessionLocal() as session:
        res = await session.execute(
            text("""
                SELECT 1
                FROM inbox
                WHERE inn = :inn
                  AND zakupka_num = :znum
            """),
            {"inn": inn, "znum": data["zakupka"]},
        )
        already_exists = res.scalar()

    if already_exists:
        # сохраняем данные в состоянии, чтобы потом использовать при подтверждении
        await state.update_data(inn=inn, company_name=name)
        await msg.answer("⚠️ Эта закупка была добавлена ранее. Удалить?")
        await state.set_state(PurchaseStates.CONFIRM_DELETE)
        return

    # --- если не дубликат, просто сохраняем ---
    async with SessionLocal() as session:
        await session.execute(
            text("""
                UPDATE inbox
                SET inn = :inn,
                    company_name = :nm
                WHERE telegram_id = :tg
                  AND zakupka_num = :znum
            """),
            {
                "inn": inn,
                "nm": name,
                "tg": msg.from_user.id,
                "znum": data["zakupka"],
            },
        )
        await session.commit()

    await msg.answer(
        "✅ Заявка сохранена и передана на обработку в 1С.\n"
        "Для добавления новой закупки нажми /start"
    )
    await state.clear()

# --- хэндлер подтверждения удаления ---
@dp.message(PurchaseStates.CONFIRM_DELETE)
async def confirm_delete(msg: Message, state: FSMContext):
    data = await state.get_data()
    answer = msg.text.lower().strip()

    if answer in ("да", "ага", "удал", "удали", "удалить"):
        async with SessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE inbox
                    SET message = 'отказались'
                    WHERE inn = :inn AND zakupka_num = :znum
                """),
                {"inn": data["inn"], "znum": data["zakupka"]},
            )
            await session.commit()

        await msg.answer("✅ Закупка помечена как 'отказались'.\n"
                         "Для добавления новой закупки нажми /start")
        await state.clear()
    else:
        await msg.answer("Ок, ничего не изменил.\n"
                        "Для добавления новой закупки нажми /start")
        await state.clear()

# ------------------------------#
# API endpoints for 1C
# ------------------------------#


from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from datetime import datetime, timedelta
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    if USE_WEBHOOK:
        if not WEBHOOK_URL:
            raise ValueError("WEBHOOK_URL not set")

        await bot.set_webhook(WEBHOOK_URL)
        print("✅ Webhook установлен:", WEBHOOK_URL)

    yield

    # shutdown
    if USE_WEBHOOK:
        await bot.delete_webhook()
        print("🛑 Webhook удалён")

    await bot.session.close()

app = FastAPI(lifespan=lifespan)

# --------------------------------
# Настройки
# --------------------------------
DB_URL = os.getenv("POSTGRES_DSN")
API_KEY = os.getenv("API_KEY") or secrets.token_urlsafe(15)

engine = create_async_engine(DB_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# --------------------------------
# Проверка API-ключа
# --------------------------------
async def check_token(api_key: str = Header(None)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

# --------------------------------
# Отдаём заявки в 1С
# --------------------------------
@app.get("/api/inbox")
async def api_inbox(api_key: str = Header(None)):
    await check_token(api_key)
    async with SessionLocal() as session:
        # Только новые (message IS NULL) или отклонённые (message='отказались')
        sql = text("""
            SELECT id, telegram_id, inn, company_name, zakupka_num, message, zakupka_number
            FROM inbox
            WHERE (message IS NULL OR message = 'отказались') AND status='new'
        """)
        res = await session.execute(sql)
        data = [dict(r._mapping) for r in res.fetchall()]
    return data



# --------------------------------
# Приём результата из 1С
# --------------------------------
@app.post("/api/result")
async def api_result(request: Request, api_key: str = Header(None)):
    await check_token(api_key)
    data = await request.json()

    async with SessionLocal() as session:
        # Обновляем данные
        await session.execute(text("""
            UPDATE inbox
               SET message = :msg,
                   zakupka_number = :zn,
                   updated_at = NOW(),
                   status = :st
             WHERE id = :id
        """), {
            "id": data.get("id"),
            "msg": data.get("message"),
            "zn": data.get("zakupka_number"),
            "st": data.get("status", "done")
        })
        await session.commit()

    # Уведомление в Telegram
    async with SessionLocal() as session:
        res = await session.execute(
            text("SELECT telegram_id FROM inbox WHERE id=:id"), {"id": data["id"]}
        )
        row = res.fetchone()

    if row:
        tg = row[0]
        # определяем текст для оповещения
        if data.get("message") == "удалена":
            txt = "❌ Закупка была удалена в 1С.\nДля добавления новой нажми /start"
        elif data.get("message") == "добавлена":
            txt = f"✅ В 1С добавлена новая закупка.\n{data.get('zakupka_number')}"
        else:
            txt = "⚠️ Обновлено состояние заявки. Нажми /start для новой."
        await bot.send_message(tg, txt, parse_mode="HTML")

    return {"ok": True}

@app.post(WEBHOOK_PATH)
async def webhook_handler(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# Healthcheck для Railway
@app.get("/")
async def root():
    return {"status": "ok"}
# ================================================================
# 🚀 Автоматическое удаление старых записей (> 2 месяцев)
# ================================================================

import asyncio
from datetime import datetime, timedelta
from sqlalchemy import text

async def cleanup_old_records_loop():
    """Фоновая задача очистки таблицы inbox"""
    while True:
        try:
            async with SessionLocal() as session:
                # Удаляем записи старше 60 дней
                await session.execute(
                    text("DELETE FROM inbox WHERE created_at < :dt"),
                    {"dt": datetime.utcnow() - timedelta(days=60)}
                )
                await session.commit()
                print("[cleanup] Старые записи удалены из inbox.")
        except Exception as e:
            print(f"[cleanup] Ошибка очистки: {e}")
        # Засыпаем на сутки (86400 секунд)
        await asyncio.sleep(86400)

@app.on_event("startup")
async def startup_event():
    """
    Событие при старте приложения — создаём фоновую задачу очистки.
    Здесь же можно инициализировать подключение к БД, бота и т.д.
    """
    asyncio.create_task(cleanup_old_records_loop())
    print("[startup] Фоновая очистка записей запущена.")

# ------------------------------#
# Start bot (современный способ)
# ------------------------------#


async def main():
    # Здесь можно добавить фоновые задачи, например FastAPI если нужно.
   async def start_polling():
    print("🚀 Запуск в режиме POLLING")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    if USE_WEBHOOK:
        # Railway режим
        import uvicorn
        print("🚀 Запуск в режиме WEBHOOK")
        uvicorn.run(
            "bot_server:app",
            host="0.0.0.0",
            port=int(os.getenv("PORT", 8000)),
        )
    else:
        # Локальный режим
        asyncio.run(start_polling())
