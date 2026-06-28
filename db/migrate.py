"""Применяет schema.sql через asyncpg (без psql)."""
import asyncio
import os
from pathlib import Path


async def migrate():
    import asyncpg
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL not set — skipping migration")
        return
    sql = (Path(__file__).parent / "schema.sql").read_text()
    conn = await asyncpg.connect(url, timeout=10)
    try:
        await conn.execute(sql)
        print("DB schema applied OK")
    except Exception as e:
        print(f"Migration note: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
