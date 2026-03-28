# database.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .models import Base
import os

DATABASE_URL = os.getenv("POSTGRES_DSN")  # например postgresql+asyncpg://user:pass@host/db

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
