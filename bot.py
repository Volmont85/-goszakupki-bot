import json
import subprocess
import os
from flask import Flask, request, jsonify
from telegram import Bot

app = Flask(__name__)

# === 🔧 Настройки ===

# Токен Telegram-бота из @BotFather
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_БОТА")

bot = Bot(token=TELEGRAM_TOKEN)

# Пути к 1С
ONEC_EXE_PATH = r"C:\Program Files\1cv8\8.3.27.1786\bin\1cv8.exe"
BASE_PATH = r"C:\Bases\MyBase"
EPF_PATH = r"C:\Scripts\СозданиеЗакупки.epf"

@app.route('/zakupka', methods=['POST'])
def zakupka():
    """
    Этот endpoint принимает JSON:
      {
        "telegram_id": 123456789,
        "user_id": 123456789,     # ID для бота
        "nomer_zakupki": "ZK-2026-01"
      }
    """
    data = request.get_json(force=True)
    telegram_id = data.get("telegram_id")
    nomer_zakupki = data.get("nomer_zakupki")
    user_id = data.get("user_id") or telegram_id   # кому бот ответит

    if not telegram_id or not nomer_zakupki:
        return jsonify({"status": "error", "message": "Не хватает данных"}), 400

    # Формируем параметры для 1С в формате JSON
    params = {
        "ТелеграмID": telegram_id,
        "НомерЗакупки": nomer_zакупки
    }
    params_str = json.dumps(params, ensure_ascii=False)

    onec_command = [
        ONEC_EXE_PATH,
        "DESIGNER",
        "/F", BASE_PATH,
        "/Execute", EPF_PATH,
        "/C", f"Параметры={params_str}"
    ]

    try:
        # Запускаем 1С
        result = subprocess.run(
            onec_command,
            capture_output=True,
            text=True,
            timeout=90
        )

        output_from_1C = result.stdout.strip()
        print("1С вернула:", output_from_1C)

        # Пробуем распарсить JSON от внешней обработки
        try:
            parsed = json.loads(output_from_1C)
        except json.JSONDecodeError:
            parsed = {"status": "error", "message": "Некорректный JSON от обработки", "raw": output_from_1C}

        # === 📨 Обработка различных статусов ===
        if parsed.get("status") == "need_inn":
            bot.send_message(chat_id=user_id, text="❗️Вы не привязаны к компании. Введите ИНН.")
        elif parsed.get("status") == "ok":
            bot.send_message(chat_id=user_id, text="✅ Закупка успешно зарегистрирована!")
        else:
            msg = parsed.get("message", "Неизвестная ошибка")
            bot.send_message(chat_id=user_id, text=f"⚠️ Ошибка: {msg}")

        return jsonify({
            "status": parsed.get("status", "unknown"),
            "message": parsed.get("message"),
            "output": output_from_1C
        }), 200

    except subprocess.TimeoutExpired:
        bot.send_message(chat_id=user_id, text="⚠️ 1С не ответила вовремя (таймаут).")
        return jsonify({"status": "error", "message": "Таймаут при выполнении 1С"}), 504

    except Exception as e:
        bot.send_message(chat_id=user_id, text=f"⚠️ Ошибка при запуске 1С: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/", methods=["GET"])
def root():
    return "✅ Flask‑интеграция с 1С и Telegram работает!"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
