import asyncio
from sqlalchemy import text
from db import SessionLocal  # импортируй свой объект сессии

async def delete_duplicates():
    """Удаляет дубликаты из таблицы inbox, оставляя самую старую запись."""
    async with SessionLocal() as session:
        # Подзапрос выбирает id всех дублей, кроме самой старой
        sql = text("""
            DELETE FROM inbox
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY inn, company_name, zakupka_num, telegram_id
                               ORDER BY created_at ASC
                           ) AS rn
                    FROM inbox
                ) t
                WHERE t.rn > 1
            )
        """)
        await session.execute(sql)
        await session.commit()
        print("✅ Дубликаты в inbox удалены.")
