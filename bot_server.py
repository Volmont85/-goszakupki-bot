# ================================================================
# IMPORTS
# ================================================================
import os
import re
import aiohttp
import asyncio
import zoneinfo
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


from database import engine
from models import Base

# ================================================================
# MODELS INIT
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
MSK = zoneinfo.ZoneInfo("Europe/Moscow")
MAIN_TG_CHAT_ID = os.getenv("MainTg")

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
# GET COMPANY BY INN
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
        print(f"[WARN] Ошибка при парсинге list-org: {e}")

    return None

# ================================================================
# START COMMAND
# ================================================================
@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    await msg.answer("👋 Привет! Пришли номер закупки (11 или 19 цифр):")
    await state.set_state(PurchaseStates.WAIT_ZAKUPKA)

# ================================================================
# STAGE 1 — ЗАКУПКА
# ================================================================
@dp.message(PurchaseStates.WAIT_ZAKUPKA)
async def handle_zakupka(msg: Message, state: FSMContext):
    num = msg.text.strip()
    if not validate_zakupka(num):
        await msg.answer("Проверь номер закупки. Для 44‑ФЗ — 19 цифр, для 223‑ФЗ — 11.")
        return

    # вставляем и получаем ID
    async with SessionLocal() as session:
        res = await session.execute(
            text("""
                INSERT INTO inbox (telegram_id, zakupka_num)
                VALUES (:tg, :num)
                RETURNING id
            """),
            {"tg": msg.from_user.id, "num": num}
        )
        zakupka_id = res.scalar_one()
        await session.commit()

    await state.update_data(zakupka=num, zakupka_id=zakupka_id)

    # проверяем связанные компании
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
        await msg.answer(f"Участвуем от «{name}» (ИНН {inn}) — всё верно?")
        await state.update_data(inn=inn, company_name=name)
        await state.set_state(PurchaseStates.CONFIRM_ONE)
    else:
        companies = "\n".join([f"{i+1}. {r[1]} (ИНН {r[0]})" for i, r in enumerate(rows)])
        await msg.answer(f"Найдено несколько компаний:\n{companies}\n\nВведи номер или новый ИНН:")
        await state.update_data(companies=rows)
        await state.set_state(PurchaseStates.CHOOSE_COMPANY)

# ================================================================
# STAGE 2 — ПОЛУЧЕНИЕ ИНН
# ================================================================
@dp.message(PurchaseStates.WAIT_INN)
async def handle_inn(msg: Message, state: FSMContext):
    inn = msg.text.strip()
    if not validate_inn(inn):
        await msg.answer("⚠️ Проверь ИНН (10 или 12 цифр).")
        return

    company = await get_company_name_by_inn(inn)
    data = await state.get_data()
    zakupka_id = data["zakupka_id"]
    username, first_name, last_name = msg.from_user.username, msg.from_user.first_name, msg.from_user.last_name

    if company:
        async with SessionLocal() as session:
            res = await session.execute(
                text("SELECT 1 FROM TelegramID WHERE telegram_id=:tg AND inn=:inn"),
                {"tg": msg.from_user.id, "inn": inn},
            )
            if not res.scalar():
                await session.execute(
                    text("""
                        INSERT INTO TelegramID (telegram_id, inn, company_name, username, first_name, last_name)
                        VALUES (:tg, :inn, :name, :username, :first_name, :last_name)
                    """),
                    {"tg": msg.from_user.id, "inn": inn, "name": company,
                     "username": username, "first_name": first_name, "last_name": last_name},
                )
            await session.execute(
                text("UPDATE inbox SET inn=:inn, company_name=:nm WHERE id=:id"),
                {"inn": inn, "nm": company, "id": zakupka_id},
            )
            await session.commit()

        await msg.answer(f"✅ ИНН {inn} принадлежит «{company}».\nЗаявка сохранена.\nДля добавления новой закупки нажми /start")
        await state.clear()
    else:
        await msg.answer("⚠️ Компания не найдена. Пришли полное название (как в ЕГРЮЛ):")
        await state.update_data(inn=inn)
        await state.set_state(PurchaseStates.WAIT_NAME)

# ================================================================
# STAGE 3 — РУЧНОЕ НАЗВАНИЕ
# ================================================================
@dp.message(PurchaseStates.WAIT_NAME)
async def handle_company_name(msg: Message, state: FSMContext):
    data = await state.get_data()
    inn, company_name = data.get("inn"), msg.text.strip()
    zakupka_id = data.get("zakupka_id")
    username, first_name, last_name = msg.from_user.username, msg.from_user.first_name, msg.from_user.last_name

    async with SessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO TelegramID (telegram_id, inn, company_name, username, first_name, last_name)
                VALUES (:tg, :inn, :name, :username, :first_name, :last_name)
            """),
            {"tg": msg.from_user.id, "inn": inn, "name": company_name,
             "username": username, "first_name": first_name, "last_name": last_name},
        )
        await session.execute(
            text("UPDATE inbox SET inn=:inn, company_name=:nm WHERE id=:id"),
            {"inn": inn, "nm": company_name, "id": zakupka_id},
        )
        await session.commit()

    await msg.answer(f"✅ Компания «{company_name}» сохранена, ИНН {inn}. Заявка передана в 1С. \nДля добавления новой закупки нажми /start")
    await state.clear()

# ================================================================
# STAGE 4 — ПОДТВЕРЖДЕНИЕ ОДНОЙ КОМПАНИИ
# ================================================================
@dp.message(PurchaseStates.CONFIRM_ONE)
async def confirm_one(msg: Message, state: FSMContext):
    if msg.text.lower() in ("да", "ага"):
        data = await state.get_data()
        zakupka_id = data["zakupka_id"]
        inn = data["inn"]
        name = data["company_name"]
        zakupka_num = data["zakupka"]

        async with SessionLocal() as session:
            # 🔍 Проверяем — не заведена ли уже такая закупка ранее для этого ИНН
            res = await session.execute(
                text("""
                    SELECT 1
                      FROM inbox
                     WHERE inn = :inn
                       AND zakupka_num = :num
                     LIMIT 1
                """),
                {"inn": inn, "num": zakupka_num},
            )
            already_exists = res.scalar()

            if already_exists:
                # ⚠️ Такая закупка уже есть — предложим удалить
                await state.update_data(inn=inn, company_name=name)
                await msg.answer("⚠️ Такая закупка уже заведена. Удалить?")
                await state.set_state(PurchaseStates.CONFIRM_DELETE)
                return

            # ✅ Если нет дубликата — обновляем запись
            await session.execute(
                text("""
                    UPDATE inbox
                       SET inn = :inn,
                           company_name = :nm
                     WHERE id = :id
                """),
                {"inn": inn, "nm": name, "id": zakupka_id},
            )
            await session.commit()

        # 💬 Ответ пользователю и очистка состояния
        await msg.answer(
            "✅ Заявка сохранена и передана на обработку в 1С.\n"
            "Для добавления новой закупки нажми /start"
        )
        await state.clear()

    else:
        # Пользователь ответил не «да» — предлагаем новый ИНН
        await msg.answer("Ок, пришли новый ИНН:")
        await state.set_state(PurchaseStates.WAIT_INN)

# ================================================================
# STAGE 5 — ВЫБОР КОМПАНИИ
# ================================================================

@dp.message(PurchaseStates.CHOOSE_COMPANY)
async def choose_company(msg: Message, state: FSMContext):
    data = await state.get_data()
    zakupka_id = data["zakupka_id"]
    text_inp = msg.text.strip()

    # 1️⃣ Если введён ИНН (10 или 12 цифр)
    if text_inp.isdigit() and len(text_inp) in (10, 12):
        await state.update_data(inn=text_inp)
        await msg.answer("🔍 Введён ИНН, продолжаем регистрацию компании.")
        await state.set_state(PurchaseStates.WAIT_INN)
        await handle_inn(msg, state)
        return

    # 2️⃣ Если введено число — проверяем, что это индекс в списке компаний
    if text_inp.isdigit():
        idx = int(text_inp) - 1
        companies = data.get("companies", [])
        if 0 <= idx < len(companies):
            inn, name = companies[idx]
            async with SessionLocal() as session:
                # проверка, нет ли уже такой записи
                res = await session.execute(
                    text("SELECT 1 FROM inbox WHERE inn=:inn AND zakupka_num=:num"),
                    {"inn": inn, "num": data["zakupka"]}
                )
                if res.scalar():
                    await state.update_data(inn=inn, company_name=name)
                    await msg.answer("⚠️ Такая закупка уже есть. Удалить?")
                    await state.set_state(PurchaseStates.CONFIRM_DELETE)
                    return

                # сохраняем выбор
                await session.execute(
                    text("UPDATE inbox SET inn=:inn, company_name=:nm WHERE id=:id"),
                    {"inn": inn, "nm": name, "id": zakupka_id},
                )
                await session.commit()

            await msg.answer(f"✅ Выбрана компания: «{name}» (ИНН {inn}). Заявка сохранена. \nДля добавления новой закупки нажми /start")
            await state.clear()
            return

        # если индекс вне диапазона
        await msg.answer("⚠️ Неверный номер фирмы. Попробуй снова.")
        return

    # 3️⃣ Если это не число вообще
    await msg.answer("⚠️ Введи номер компании из списка или ИНН (10 или 12 цифр).")

# ================================================================
# CONFIRM DELETE
# ================================================================
@dp.message(PurchaseStates.CONFIRM_DELETE)
async def confirm_delete(msg: Message, state: FSMContext):
    data = await state.get_data()
    inn = data.get("inn")
    zakupka_num = data.get("zakupka")
    zakupka_id = data.get("zakupka_id")

    # нормализуем ответ пользователя
    user_answer = msg.text.lower().strip()

    if user_answer in ("да", "ага", "удал", "удали", "удалить"):
        async with SessionLocal() as session:
            # проверяем наличие закупки с этим ИНН и номером
            res = await session.execute(
                text("SELECT id FROM inbox WHERE inn=:inn AND zakupka_num=:num"),
                {"inn": inn, "num": zakupka_num},
            )
            row = res.fetchone()

            if not row:
                await msg.answer("⚠️ Не нашёл закупку с этим ИНН и номером закупки.")
                await state.clear()
                return
            
            # обновляем нужную запись
            await session.execute(
                text("UPDATE inbox SET message='отказались', status='new' WHERE inn=:inn AND zakupka_num=:num"),
                {"inn": inn, "num": zakupka_num},
            )
            await session.commit()

        await msg.answer(
            "✅ Закупка помечена как «отказались».\n"
            "Для добавления новой закупки нажми /start"
        )
    else:
        await msg.answer("👌 Ок, ничего не изменил.\nДля добавления новой закупки нажми /start")

    await state.clear()
# ================================================================
# CLEANUP TASKS
# ================================================================
async def cleanup_old_records_loop():
    while True:
        try:
            async with SessionLocal() as session:
                res = await session.execute(
                    text("DELETE FROM inbox WHERE created_at < :dt"),
                    {"dt": datetime.utcnow() - timedelta(days=60)}
                )
                await session.commit()
                print(f"[cleanup] Старых записей удалено: {res.rowcount}")
        except Exception as e:
            print(f"[cleanup] Ошибка очистки: {e}")
        await asyncio.sleep(86400)

async def cleanup_null_records_loop():
    while True:
        try:
            async with SessionLocal() as session:
                cutoff = datetime.utcnow() - timedelta(hours=2)
                res = await session.execute(
                    text("""
                        DELETE FROM inbox
                        WHERE (inn IS NULL OR TRIM(inn) = '')
                          AND created_at < :dt
                    """),
                    {"dt": cutoff}
                )
                await session.commit()
                print(f"[cleanup] Удалено {res.rowcount} пустых строк.")
        except Exception as e:
            print(f"[cleanup] Ошибка очистки NULL: {e}")
        await asyncio.sleep(3600)

async def cleanup_duplicates_loop():
    """Фоновая задача: каждые 15 минут удаляет дубликаты из inbox."""
    while True:
        try:
            async with SessionLocal() as session:
                res = await session.execute(text("""
                    DELETE FROM inbox
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY inn, company_name, zakupka_num, telegram_id
                                       ORDER BY created_at ASC
                                   ) AS rn
                            FROM inbox
                        ) t
                        WHERE t.rn > 1
                    )
                """))
                await session.commit()
                print(f"[cleanup] {datetime.utcnow():%Y-%m-%d %H:%M:%S} — удалено {res.rowcount} дублей.")
        except Exception as e:
            print(f"[cleanup] Ошибка очистки дублей: {e}")
        await asyncio.sleep(900)  # 15 минут

async def cleanup_old_deadlines_loop():
    while True:
        try:
            async with SessionLocal() as session:
                res = await session.execute(
                    text("DELETE FROM zakupka_deadlines WHERE deadline < :dt"),
                    {"dt": datetime.utcnow() - timedelta(days=60)}
                )
                await session.commit()
                print(f"[cleanup] Старых дедлайнов удалено: {res.rowcount}")
        except Exception as e:
            print(f"[cleanup] Ошибка очистки дедлайнов: {e}")
        await asyncio.sleep(86400)
# ================================================================
# НАПОМИНАНИЯ "ЗАЯВКА ПОДАНА" (интеграция с 1С)
# ================================================================
async def deadline_reminder_loop():
    """В 17:00 МСК за день до дедлайна (и далее каждые 2 часа, пока нет ответа
    "Да") шлёт вопрос с кнопками Да/Нет в MainTg."""
    while True:
        try:
            now_msk = datetime.now(MSK)
            if now_msk.hour >= 17 and MAIN_TG_CHAT_ID:
                async with SessionLocal() as session:
                    res = await session.execute(
                        text("""
                            SELECT id, zakupka_num, zakazchik, deadline, last_asked_at
                              FROM zakupka_deadlines
                             WHERE submitted = false
                               AND confirmed_at IS NULL
                        """)
                    )
                    rows = res.fetchall()

                    for row_id, zakupka_num, zakazchik, deadline, last_asked_at in rows:
                        deadline_msk = deadline.replace(tzinfo=MSK) if deadline.tzinfo is None else deadline.astimezone(MSK)
                        if deadline_msk.date() != (now_msk + timedelta(days=1)).date():
                            continue
                        if last_asked_at and (now_msk.replace(tzinfo=None) - last_asked_at) < timedelta(hours=2):
                            continue

                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="Да", callback_data=f"zayavka_da_{row_id}"),
                            InlineKeyboardButton(text="Нет", callback_data=f"zayavka_net_{row_id}"),
                        ]])
                        text_msg = (
                            f"Заявка подана по закупке №{zakupka_num}"
                            f"{' (' + zakazchik + ')' if zakazchik else ''}?\n"
                            f"Дедлайн: {deadline_msk.strftime('%d.%m.%Y %H:%M')}"
                        )
                        await bot.send_message(int(MAIN_TG_CHAT_ID), text_msg, reply_markup=kb)

                        await session.execute(
                            text("""
                                UPDATE zakupka_deadlines
                                   SET last_asked_at = :now, ask_count = ask_count + 1
                                 WHERE id = :id
                            """),
                            {"now": now_msk.replace(tzinfo=None), "id": row_id},
                        )
                    await session.commit()
        except Exception as e:
            print(f"[deadline_reminder_loop] error: {e}")
        await asyncio.sleep(300)  # раз в 5 минут - ловит и 17:00, и повтор через 2ч

@dp.callback_query(lambda c: c.data and c.data.startswith("zayavka_"))
async def handle_zayavka_answer(callback: CallbackQuery):
    action, row_id = callback.data.rsplit("_", 1)
    async with SessionLocal() as session:
        row = (await session.execute(
            text("SELECT id FROM zakupka_deadlines WHERE id = :id"),
            {"id": int(row_id)},
        )).fetchone()
        if not row:
            await callback.answer("Запись не найдена")
            return

        if action == "zayavka_da":
            await session.execute(
                text("UPDATE zakupka_deadlines SET submitted = true, confirmed_at = :now WHERE id = :id"),
                {"now": datetime.utcnow(), "id": int(row_id)},
            )
            await session.commit()
            await callback.message.edit_text(callback.message.text + "\n\n✅ Подтверждено: подана")
        else:
            await session.execute(
                text("UPDATE zakupka_deadlines SET last_asked_at = :now WHERE id = :id"),
                {"now": datetime.now(MSK).replace(tzinfo=None), "id": int(row_id)},
            )
            await session.commit()
            await callback.message.edit_text(callback.message.text + "\n\n❌ Ещё не подана, спрошу через 2 часа")
    await callback.answer()

# ================================================================
# Сброс статуса в inbox
# ================================================================

async def ensure_deadlines_table():
    async with SessionLocal() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS zakupka_deadlines (
                id SERIAL PRIMARY KEY,
                id1c VARCHAR UNIQUE NOT NULL,
                zakupka_num VARCHAR NOT NULL,
                zakazchik VARCHAR,
                deadline TIMESTAMP NOT NULL,
                submitted BOOLEAN DEFAULT false,
                last_asked_at TIMESTAMP,
                ask_count INTEGER DEFAULT 0,
                confirmed_at TIMESTAMP,
                acked BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            )
        """))
        await session.commit()

async def reset_stuck_processes():
    while True:
        try:
            async with SessionLocal() as session:
                now = datetime.utcnow()
                ttl_limit = now - timedelta(minutes=5)

                await session.execute(
                    text("""
                        UPDATE inbox
                           SET status = 'new',
                               updated_at = :now
                         WHERE status = 'in_process'
                           AND updated_at < :ttl
                    """),
                    {"now": now, "ttl": ttl_limit}
                )
                await session.commit()

        except Exception as e:
            print(f"[reset_stuck_processes] error: {e}")

        await asyncio.sleep(60)
# ================================================================
# FASTAPI STARTUP
# ================================================================
@app.on_event("startup")
async def startup_event():
    await ensure_deadlines_table()

    # --- ВРЕМЕННО (убрать после проверки) — разовая диагностика по
    # закупке 0848300053826000262: есть ли запись/дедлайн, и форс-отправка
    # тестового вопроса Да/Нет в MainTg, минуя окно 17:00/throttling.
    async with SessionLocal() as _debug_session:
        _debug_res = await _debug_session.execute(
            text("SELECT * FROM zakupka_deadlines WHERE zakupka_num = :num ORDER BY updated_at DESC LIMIT 1"),
            {"num": "0848300053826000262"},
        )
        _debug_row = _debug_res.mappings().first()
        print(f"[DEBUG force_ask] zakupka_num=0848300053826000262 найдено: {dict(_debug_row) if _debug_row else None}")
        if _debug_row and MAIN_TG_CHAT_ID:
            _debug_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Да", callback_data=f"zayavka_da_{_debug_row['id']}"),
                InlineKeyboardButton(text="Нет", callback_data=f"zayavka_net_{_debug_row['id']}"),
            ]])
            _debug_deadline_str = _debug_row["deadline"].strftime("%d.%m.%Y %H:%M") if _debug_row["deadline"] else "не указан"
            _debug_text = (
                f"[ТЕСТ] Заявка подана по закупке №{_debug_row['zakupka_num']}"
                f"{' (' + _debug_row['zakazchik'] + ')' if _debug_row['zakazchik'] else ''}?\n"
                f"Дедлайн: {_debug_deadline_str}"
            )
            await bot.send_message(int(MAIN_TG_CHAT_ID), _debug_text, reply_markup=_debug_kb)
            print("[DEBUG force_ask] тестовое сообщение отправлено в MainTg")
        elif not _debug_row:
            print("[DEBUG force_ask] записи с таким zakupka_num в БД нет")
    # --- конец временного блока ---

    asyncio.create_task(cleanup_old_records_loop())
    asyncio.create_task(cleanup_null_records_loop())
    asyncio.create_task(cleanup_duplicates_loop())
    asyncio.create_task(cleanup_old_deadlines_loop())
    asyncio.create_task(reset_stuck_processes())
    asyncio.create_task(deadline_reminder_loop())
    asyncio.create_task(dp.start_polling(bot))
    print("[startup] Bot polling + cleanup started")

# ================================================================
# ROUTERS
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
