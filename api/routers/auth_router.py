"""Авторизация: вход супер-админа и агента."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import database as db
from auth import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# SEC-003: Pre-computed dummy hash to equalize response time when username is not found
DUMMY_HASH = hash_password("dummy")


class LoginBody(BaseModel):
    username: str = Field(max_length=100)   # SEC-005
    password: str = Field(max_length=1000)  # SEC-005


@router.post("/admin/login")
async def admin_login(body: LoginBody):
    admin = await db.fetchrow(
        "SELECT id, username, password_hash FROM admin_users WHERE username = $1",
        body.username,
    )
    # SEC-003: Always run bcrypt to prevent timing-based username enumeration
    stored_hash = admin["password_hash"] if admin is not None else DUMMY_HASH
    # Пароль в схеме создан через crypt(bf) -> совместим с passlib bcrypt
    if not verify_password(body.password, stored_hash) or admin is None:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_token(admin["id"], "admin")
    return {"token": token, "role": "admin", "username": admin["username"]}


@router.post("/agent/login")
async def agent_login(body: LoginBody):
    agent = await db.fetchrow(
        "SELECT id, name, username, password_hash, is_active FROM agents WHERE username = $1",
        body.username,
    )
    # SEC-003: Always run bcrypt to prevent timing-based username enumeration
    stored_hash = agent["password_hash"] if (agent is not None and agent["password_hash"]) else DUMMY_HASH
    password_ok = verify_password(body.password, stored_hash)

    if agent is None or not agent["password_hash"] or not password_ok:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    # SEC-004: is_active checked AFTER successful password verification to prevent
    # username enumeration via 401 vs 403 status code difference
    if not agent["is_active"]:
        raise HTTPException(status_code=403, detail="Агент заблокирован")

    token = create_token(agent["id"], "agent")
    return {"token": token, "role": "agent", "name": agent["name"]}
