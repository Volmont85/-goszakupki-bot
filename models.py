from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Inbox(Base):
    __tablename__ = "inbox"  # обязательно с __

    id = Column(Integer, primary_key=True)
    inn = Column(String, nullable=False)
    zakupka_num = Column(String, nullable=False)
    company_name = Column(String)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("inn", "zakupka_num", name="uix_inn_zakupka"),
    )
