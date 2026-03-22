import os
import logging
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Настройки логов ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://my-bot.up.railway.app
ONEC_URL = os.getenv("ONEC_URL", "https://apps.itscloud.ru/00000276_3/hs/botapi/ping")
ONEC_USER = os.getenv("ONEC_USER", "user")
ONEC_PASS = os.getenv("ONEC_PASS", "pass")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения Railway!")

# --- Создание Telegram-приложения ---
application = Application.builder().token(BOT_TOKEN).build()

# =========================================================
# 📌 Команды Telegram
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на /start"""
    await update.message.reply_text(
        "👋 Привет!\n"
        "Бот запущен и готов к работе.\n"
        "Используй /test — проверим связь с 1С."
    )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка тестового запроса в 1С"""
    chat_id = update.effective_chat.id
    await update.message.reply_text("📡 Отправляю запрос в 1С...")

    try:
        response = requests.post(
            ONEC_URL,
            json={"TelegramID": chat_id},
            auth=(ONEC_USER, ONEC_PASS),
            timeout=20
        )

        text = response.text.strip()

        # Если 1С возвращает слишком длинный ответ — обрезаем
        if len(text) > 4000:
            text = text[:3990] + "\n\n... (обрезано, ответ слишком длинный)"

        await update.message.reply_text(f"✅ Ответ от 1С:\n{text}")

    except Exception as e:
        err = str(e)
        if len(err) > 3000:
            err = err[:3000] + "..."
        await update.message.reply_text(f"❌ Ошибка при обращении к 1С:\n{err}")

# =========================================================
# 🔧 Регистрация команд
# =========================================================
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("test", test_command))

# =========================================================
# 🧩 Flask для webhook
# =========================================================
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "✅ Railway бот запущен и слушает Telegram."

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.update_queue.put(update)
    return "ok", 200

# =========================================================
# 🚀 Точка входа
# =========================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))

    if WEBHOOK_URL and WEBHOOK_URL.startswith("https://"):
        logging.info(f"Запуск webhook на {WEBHOOK_URL}/{BOT_TOKEN}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        logging.warning("WEBHOOK_URL не задан или без HTTPS — запуск в режиме polling.")
        application.run_polling()
