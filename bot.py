import os, requests, logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Логи для Railway
logging.basicConfig(level=logging.INFO)

# === Конфигурация ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "<ТОКЕН_БОТА>")
ONEC_URL = os.getenv("ONEC_URL", "https://yourcompany.itscloud.ru/testservice")
ONEC_USER = os.getenv("ONEC_USER", "user")
ONEC_PASS = os.getenv("ONEC_PASS", "pass")

# Приложение Telegram
application = Application.builder().token(BOT_TOKEN).build()

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Используй /test — проверим связь с 1С!")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправляю запрос в 1С...")
    try:
        response = requests.post(
            ONEC_URL,
            json={"TelegramID": update.effective_chat.id},
            auth=(ONEC_USER, ONEC_PASS),
            timeout=20
        )
        await update.message.reply_text(f"Ответ от 1С: {response.text}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при обращении к 1С: {e}")

# Регистрируем команды
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("test", test_command))

# === Flask ===
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Railway бот запущен"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.update_queue.put(update)
    return "ok", 200

if __name__ == "__main__":
    # Запуск webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        url_path=BOT_TOKEN
    )
