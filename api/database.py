"""Пул соединений asyncpg."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import asyncpg
from config import settings

_pool: Optional[asyncpg.Pool] = None
_log = logging.getLogger("scenti.db")


class DatabaseUnavailableError(RuntimeError):
    """Raised when the DB pool is not initialised or all connections are broken."""


async def connect() -> bool:
    """Создать пул соединений при старте приложения. Возвращает True при успехе."""
    global _pool
    if _pool is not None:
        return True
    for attempt in range(3):
        try:
            _pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    dsn=settings.DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    command_timeout=30,
                    # Recycle idle connections after 60 s to prevent stale-connection
                    # failures when the DB server IP changes on Railway redeploy.
                    max_inactive_connection_lifetime=60,
                    ssl="require" if "sslmode=require" in settings.DATABASE_URL else None,
                ),
                timeout=15,
            )
            _log.info("DB connected on attempt %d", attempt + 1)
            return True
        except Exception as exc:
            _log.error("DB connect attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(3)
    return False


async def disconnect() -> None:
    """Закрыть пул при остановке."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the active pool or raise DatabaseUnavailableError (maps to HTTP 503)."""
    if _pool is None:
        raise DatabaseUnavailableError("Пул БД не инициализирован")
    return _pool


async def _acquire_with_retry(pool: asyncpg.Pool):
    """Acquire a connection; on connection-level error expire stale connections and retry once."""
    try:
        return pool.acquire()
    except (asyncpg.PostgresConnectionError, OSError):
        _log.warning("DB acquire failed, expiring stale connections and retrying once")
        pool.expire_connections()
        return pool.acquire()


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
