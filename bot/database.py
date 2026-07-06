"""Пул соединений asyncpg + запросы для бота.

Работает с той же БД, что и FastAPI (db/schema.sql).
Если БД недоступна — бот всё равно стартует, а is_available() вернёт False,
и хендлеры ответят «Сервис временно недоступен».
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def connect() -> bool:
    """Создать пул соединений при старте. Возвращает True при успехе."""
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
                    ssl="require" if "sslmode=require" in settings.DATABASE_URL else None,
                ),
                timeout=15,
            )
            logger.info("Подключение к БД установлено (попытка %d)", attempt + 1)
            return True
        except Exception as exc:  # noqa: BLE001 — стартуем даже без БД
            _pool = None
            logger.error("DB attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(3)
    return False


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def is_available() -> bool:
    return _pool is not None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Пул БД не инициализирован")
    return _pool


# --- низкоуровневые хелперы (параметризованные запросы, без конкатенации) ---

async def fetch(query: str, *args) -> list[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetchrow(query: str, *args) -> Optional[dict[str, Any]]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(query, *args)
    return dict(row) if row else None


async def fetchval(query: str, *args):
    async with get_pool().acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    async with get_pool().acquire() as conn:
        return await conn.execute(query, *args)


# --- доменные запросы ---

async def get_user_by_phone(phone: str) -> Optional[dict[str, Any]]:
    return await fetchrow("SELECT id FROM users WHERE phone = $1 AND is_active = TRUE", phone)


async def get_user_by_telegram_id(telegram_id: int) -> Optional[dict[str, Any]]:
    return await fetchrow(
        "SELECT * FROM users WHERE telegram_id = $1",
        telegram_id,
    )


async def get_privacy_policy(lang: str = "ru") -> dict[str, str]:
    column = "content_uz" if lang == "uz" else "content_ru"
    row = await fetchrow(
        f"SELECT {column} AS content, file_url, file_url_uz, tg_file_id, tg_file_id_uz FROM privacy_policy ORDER BY id LIMIT 1"
    )
    if not row:
        return {"text": "Политика конфиденциальности временно недоступна.", "file_url": "", "tg_file_id": ""}
    is_uz = lang == "uz"
    return {
        "text": row.get("content") or ("Maxfiylik siyosati vaqtincha mavjud emas." if is_uz else "Политика конфиденциальности временно недоступна."),
        "file_url": (row.get("file_url_uz") or row.get("file_url") or "") if is_uz else (row.get("file_url") or ""),
        "tg_file_id": (row.get("tg_file_id_uz") or row.get("tg_file_id") or "") if is_uz else (row.get("tg_file_id") or ""),
    }


async def get_privacy_policy_text(lang: str = "ru") -> str:
    return (await get_privacy_policy(lang))["text"]


async def get_diffusers() -> list[dict[str, Any]]:
    return await fetch(
        "SELECT id, name_ru, name_uz FROM diffusers WHERE is_active = TRUE AND type = 'device' ORDER BY sort_order LIMIT 4"
    )


async def get_regions() -> list[dict[str, Any]]:
    return await fetch("SELECT id, name_ru, name_uz FROM regions ORDER BY name_ru")


async def get_region(region_id: int) -> Optional[dict[str, Any]]:
    return await fetchrow("SELECT id, name_ru, name_uz FROM regions WHERE id = $1", region_id)


async def get_districts_by_region(region_id: int) -> list[dict[str, Any]]:
    return await fetch(
        "SELECT id, name_ru, name_uz FROM districts WHERE region_id = $1 ORDER BY name_ru",
        region_id,
    )


async def get_district(district_id: int) -> Optional[dict[str, Any]]:
    return await fetchrow(
        "SELECT id, region_id, name_ru, name_uz FROM districts WHERE id = $1",
        district_id,
    )


async def upsert_user(
    *,
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    business_name: str,
    phone: str,
    region_id: int,
    district_id: int,
    diffuser_id: Optional[int] = None,
    language: str = "ru",
) -> dict[str, Any]:
    """Создать или обновить пользователя по telegram_id, отметив согласие с политикой."""
    row = await fetchrow(
        """
        INSERT INTO users (
            telegram_id, username, first_name, last_name,
            business_name, phone, region_id, district_id,
            diffuser_id, language, privacy_accepted, is_active
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, TRUE, TRUE)
        ON CONFLICT (telegram_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            business_name = EXCLUDED.business_name,
            phone = EXCLUDED.phone,
            region_id = EXCLUDED.region_id,
            district_id = EXCLUDED.district_id,
            diffuser_id = EXCLUDED.diffuser_id,
            language = EXCLUDED.language,
            privacy_accepted = TRUE,
            is_active = TRUE,
            cashback_balance   = CASE WHEN users.is_active = FALSE THEN 0          ELSE users.cashback_balance   END,
            cashback_reset_at  = CASE WHEN users.is_active = FALSE THEN NOW()      ELSE users.cashback_reset_at  END
        RETURNING *
        """,
        telegram_id,
        username,
        first_name,
        last_name,
        business_name,
        phone,
        region_id,
        district_id,
        diffuser_id,
        language,
    )
    return row  # type: ignore[return-value]


async def accept_privacy(telegram_id: int) -> None:
    await execute(
        "UPDATE users SET privacy_accepted = TRUE WHERE telegram_id = $1",
        telegram_id,
    )


async def update_language(telegram_id: int, lang: str) -> None:
    await execute(
        "UPDATE users SET language = $1 WHERE telegram_id = $2",
        lang, telegram_id,
    )


async def get_all_user_telegram_ids() -> list[int]:
    rows = await fetch(
        "SELECT telegram_id FROM users WHERE is_active = TRUE AND telegram_id IS NOT NULL"
    )
    return [r["telegram_id"] for r in rows]
