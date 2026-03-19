
import os, logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = Application.builder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает через Render!")

application.add_handler(CommandHandler("start", start))

if __name__ == "__main__":
    logger.info("🚀 Starting webhook server...")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}",
    )
