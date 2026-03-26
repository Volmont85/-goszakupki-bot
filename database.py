# database.py

import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

DB_URL = os.getenv("POSTGRES_DSN")

engine = create_async_engine(DB_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
