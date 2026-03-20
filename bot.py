from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests, asyncio, os

print("==== Railway ENV ====")
for k, v in os.environ.items():
    if "BOT" in k or "RAILWAY" in k or "ONEC" in k:
        print(k, "=", v)
print("======================")

# --- Конфигурация ---
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Адреса API 1С
ONEC_API = os.environ.get("ONEC_API", "https://apps.itscloud.ru/00000276_3/hs/botapi/receive").strip()
ONEC_API_INN = os.environ.get("ONEC_API_INN", "https://apps.itscloud.ru/00000276_3/hs/botapi/receiveINN").strip()

# Логин и пароль для доступа (укажи в Railway Variables)
ONEC_LOGIN = os.environ.get("ONEC_LOGIN", "").strip()
ONEC_PASSWORD = os.environ.get("ONEC_PASSWORD", "").strip()

VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() == "true"

# --- Flask app ---
app = Flask(__name__)

# --- Telegram приложение ---
application = Application.builder().token(TOKEN).build()

# --- вспомогательная функция для запросов к 1С ---
def post_to_1c(url, payload):
    """Отправляет POST‑запрос к 1С и возвращает dict"""
    try:
        print(f"DEBUG → POST {url} payload={payload}")

        # ✅ добавляем basic auth
        auth = None
        if ONEC_LOGIN and ONEC_PASSWORD:
            auth = (ONEC_LOGIN, ONEC_PASSWORD)

        r = requests.post(url, json=payload, timeout=15, verify=VERIFY_SSL, auth=auth)
        print("DEBUG статус:", r.status_code)

        if r.status_code == 200:
            return r.json()
        elif r.status_code == 401:
            return {"status": "error", "message": "⛔ Ошибка авторизации на сервере 1С (проверь логин/пароль и режим доступа)"}
        else:
            return {"status": "error", "message": f"⚠️ 1С ответил кодом {r.status_code}: {r.text[:150]}"}

    except requests.exceptions.Timeout:
        return {"status": "error", "message": "⌛ Сервер 1С не ответил за таймаут 15 c."}
    except requests.exceptions.ConnectionError as e:
        return {"status": "error", "message": f"🌐 Ошибка соединения: {e}"}
    except requests.exceptions.SSLError:
        return {"status": "error", "message": "🔒 SSL‑ошибка: проверь сертификат (VERIFY_SSL=false)"}
    except Exception as e:
        return {"status": "error", "message": f"❗ Непредвиденная ошибка: {e}"}

# --- команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Здравствуйте! Отправьте номер закупки, чтобы начать.")
    await update.message.reply_text("ℹ️ Пример: 0161150001726000006")

# --- состояние пользователей ---
user_states = {}

# --- обработка сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_states.get(user_id) == "waiting_inn":
        data = post_to_1c(ONEC_API_INN, {"ИНН": text})
        await update.message.reply_text(data.get("message", "⚠️ Ошибка запроса к 1С"))
        user_states.pop(user_id, None)
        return

    data = post_to_1c(ONEC_API, {"TelegramID": str(user_id), "НомерЗакупки": text})
    status = data.get("status")
    msg = data.get("message", "Нет ответа")

    if status == "ok":
        await update.message.reply_text(f"✅ {msg}")
    elif status == "ask_inn":
        await update.message.reply_text(msg)
        user_states[user_id] = "waiting_inn"
    else:
        await update.message.reply_text(msg)

# --- регистрируем обработчики ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Flask маршруты ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.run(application.process_update(update))
    return "ok", 200

@app.route("/")
def home():
    return "Bot is running!", 200

# --- запуск на Railway ---
if __name__ == "__main__":
    HOST = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not HOST:
        print("⚠️ Добавьте переменную RAILWAY_PUBLIC_DOMAIN в Railway → Variables")

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )
