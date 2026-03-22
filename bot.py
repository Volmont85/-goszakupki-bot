import os, json, requests
from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler
from telegram.utils.request import Request

app = Flask(__name__)

# === 🔧 Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "<ТОКЕН_БОТА>")
ONEC_URL = os.getenv("ONEC_URL", "https://apps.itscloud.ru/00000276_3/hs/botapi/ping")  # URL твоего HTTP‑сервиса 1С
ONEC_LOGIN = os.getenv("ONEC_LOGIN", "botuser")  # логин 1С‑пользователя
ONEC_PASSWORD = os.getenv("ONEC_PASSWORD", "botpass")  # пароль

bot = Bot(token=BOT_TOKEN)

# === /start ===
def start(update: Update, _):
    update.message.reply_text("Привет! Отправь /test для проверки связи с 1С 🚀")

# === /test ===
def test_command(update: Update, _):
    user_id = update.effective_chat.id
    update.message.reply_text("Отправляю запрос в 1С...")

    try:
        response = requests.post(
            ONEC_URL,
            json={"TelegramID": user_id},
            auth=(ONEC_USER, ONEC_PASS),
            timeout=20
        )
        result = response.text
        update.message.reply_text(f"Ответ от 1С: {result}")

    except Exception as e:
        update.message.reply_text(f"❌ Ошибка при обращении к 1С: {e}")

# === Для webhook Railway ===
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dp.process_update(update)
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "✅ Railway‑бот запущен"

# === Telegram dispatcher ===
request_ = Request(con_pool_size=8)
dp = Dispatcher(bot, None, workers=0)
dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("test", test_command))

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 8080)), host="0.0.0.0")
