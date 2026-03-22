import os, logging, requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "<ТОКЕН_БОТА>")
ONEC_URL = os.getenv("ONEC_URL", "https://yourcompany.itscloud.ru/testservice")
ONEC_USER = os.getenv("ONEC_USER", "botuser")
ONEC_PASS = os.getenv("ONEC_PASS", "botpass")

# Создаём Telegram‑приложение
application = Application.builder().token(BOT_TOKEN).build()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши /test для проверки связи с 1С 👋")


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


@app.route("/", methods=["GET"])
def home():
    return "✅ Railway‑бот запущен"


@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    await application.update_queue.put(Update.de_json(data, application.bot))
    return "ok", 200


if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8080)),
        url_path=BOT_TOKEN
    )
