# bot_instance.py
from aiogram import Bot
import os

bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
