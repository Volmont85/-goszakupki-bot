import os
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

# =========================
# НАСТРОЙКИ
# =========================
TOKEN = os.getenv("BOT_TOKEN")  # Лучше хранить токен в переменной окружения Render
PORT = int(os.environ.get("PORT", 10000))
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

# =========================
# ЛОГИРОВАНИЕ
# =========================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЙ
# =========================
app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# =========================
# ОБРАБОТЧИКИ КОМАНД
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Бот успешно работает через Render и готов принимать команды.\n"
        "Попробуй /version чтобы узнать версию сборки."
    )

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔧 Текущая версия сборки: 2026‑03‑19")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("version", version))

# =========================
# МАРШРУТЫ FLASK
# =========================
@app.route("/", methods=["GET"])
def index():
    """Проверка, что сервер активен."""
    return "✅ Bot is running on Render!", 200

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    """Получаем обновления от Telegram."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.create_task(application.process_update(update))
    return "ok", 200

# =========================
# ЗАПУСК
# =========================
if __name__ == "__main__":
    logger.info("🚀 Запуск сервера Flask и настройка webhook...")
    logger.info(f"RENDER_EXTERNAL_HOSTNAME = {RENDER_HOST}")
    logger.info(f"Порт = {PORT}")

    # Запуск вебхука (PTB встроенный aiohttp-сервер)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{RENDER_HOST}/{TOKEN}",
    )

    logger.info("✅ Webhook успешно запущен и бот активен на Render!")
