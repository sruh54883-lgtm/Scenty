from __future__ import annotations
"""Зависимости FastAPI: текущий пользователь WebApp, админ, агент."""
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import database as db
from auth import decode_token, parse_telegram_init_data

bearer_scheme = HTTPBearer(auto_error=False)


# ---------- WebApp клиент (Telegram) ----------
async def get_current_user(
    x_telegram_data: str | None = Header(default=None, alias="X-Telegram-Data"),
    x_telegram_id: str | None = Header(default=None, alias="X-Telegram-Id"),
) -> dict:
    """
    Авторизация клиента WebApp.
    1) X-Telegram-Data: валидируем подпись initData -> telegram_id
    2) X-Telegram-Id (dev fallback): берём id напрямую
    Создаёт пользователя в БД при первом обращении.
    """
    telegram_id: int | None = None
    tg_user: dict = {}

    if x_telegram_data:
        tg_user = parse_telegram_init_data(x_telegram_data) or {}
        if tg_user.get("id"):
            telegram_id = int(tg_user["id"])

    if telegram_id is None and x_telegram_id:
        try:
            telegram_id = int(x_telegram_id)
        except ValueError:
            telegram_id = None

    if telegram_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не удалось определить Telegram пользователя",
        )

    user = await db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
    if user is None:
        user = await db.fetchrow(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name, language)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            telegram_id,
            tg_user.get("username"),
            tg_user.get("first_name"),
            tg_user.get("last_name"),
            (tg_user.get("language_code") or "ru")[:2] if tg_user.get("language_code") in ("ru", "uz") else "ru",
        )

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Пользователь заблокирован")
    return user


# ---------- JWT (admin / agent) ----------
async def _get_payload(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return payload


async def get_current_admin(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    admin = await db.fetchrow(
        "SELECT id, username, is_superadmin FROM admin_users WHERE id = $1",
        int(payload["sub"]),
    )
    if admin is None:
        raise HTTPException(status_code=401, detail="Администратор не найден")
    return admin


async def get_current_agent(payload: dict = Depends(_get_payload)) -> dict:
    if payload.get("role") != "agent":
        raise HTTPException(status_code=403, detail="Доступ только для агента")
    agent = await db.fetchrow(
        "SELECT id, name, phone, username, is_active FROM agents WHERE id = $1",
        int(payload["sub"]),
    )
    if agent is None:
        raise HTTPException(status_code=401, detail="Агент не найден")
    if not agent["is_active"]:
        raise HTTPException(status_code=403, detail="Агент заблокирован")
    return agent
