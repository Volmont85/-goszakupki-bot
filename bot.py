from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import asyncio, os, requests

# --- Настройки и переменные окружения ---
TOKEN = os.environ.get("BOT_TOKEN")
ONEC_API = "https://apps.itscloud.ru/00000276_3/hs/botapi/receive"
ONEC_API_INN = "https://apps.itscloud.ru/00000276_3/hs/botapi/receiveINN"

app = Flask(__name__)

# Создаём Telegram-приложение
application = Application.builder().token(TOKEN).build()

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Здравствуйте! Отправьте номер закупки, чтобы начать.")

# --- Обработка сообщений ---
user_states = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_states.get(user_id) == "waiting_inn":
        r = requests.post(ONEC_API_INN, json={"ИНН": text})
        data = r.json()
        msg = data.get("message", "Ошибка запроса")
        await update.message.reply_text(msg)
        user_states.pop(user_id, None)
        return

    r = requests.post(ONEC_API, json={"TelegramID": str(user_id), "НомерЗакупки": text})
    try:
        data = r.json()
    except:
        await update.message.reply_text("Ошибка: сервер 1С не ответил.")
        return

    if data.get("status") == "ok":
        await update.message.reply_text(f"✅ {data.get('message')}")
    elif data.get("status") == "ask_inn":
        await update.message.reply_text(data.get("message"))
        user_states[user_id] = "waiting_inn"
    else:
        await update.message.reply_text(f"⚠️ {data.get('message')}")

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Flask webhook обработчик ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.run(application.process_update(update))
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!", 200

if __name__ == "__main__":
    # Запуск в среде Render: webhook + Flask
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        url_path=TOKEN,
        webhook_url=f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN в Railway')}/{TOKEN}"
    )

