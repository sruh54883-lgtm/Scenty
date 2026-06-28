"""Пул соединений asyncpg."""
import asyncio
import logging

import asyncpg
from config import settings

_pool: asyncpg.Pool | None = None
_log = logging.getLogger("scenti.db")


async def connect() -> bool:
    """Создать пул соединений при старте приложения. Возвращает True при успехе."""
    global _pool
    if _pool is not None:
        return True
    try:
        _pool = await asyncio.wait_for(
            asyncpg.create_pool(
                dsn=settings.DATABASE_URL,
                min_size=1,
                max_size=10,
                command_timeout=30,
            ),
            timeout=8,
        )
        _log.info("DB connected")
        return True
    except Exception as exc:
        _log.error("DB connect failed: %s", exc)
        return False


async def disconnect() -> None:
    """Закрыть пул при остановке."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Пул БД не инициализирован")
    return _pool


async def fetch(query: str, *args):
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetchrow(query: str, *args):
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *args)
    return dict(row) if row else None


async def fetchval(query: str, *args):
    async with get_pool().acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    async with get_pool().acquire() as conn:
        return await conn.execute(query, *args)
