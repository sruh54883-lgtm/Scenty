"""Авторизация: вход супер-админа и агента."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import database as db
from auth import create_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/admin/login")
async def admin_login(body: LoginBody):
    admin = await db.fetchrow(
        "SELECT id, username, password_hash FROM admin_users WHERE username = $1",
        body.username,
    )
    if admin is None:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    # Пароль в схеме создан через crypt(bf) -> совместим с passlib bcrypt
    if not verify_password(body.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_token(admin["id"], "admin")
    return {"token": token, "role": "admin", "username": admin["username"]}


@router.post("/agent/login")
async def agent_login(body: LoginBody):
    agent = await db.fetchrow(
        "SELECT id, name, username, password_hash, is_active FROM agents WHERE username = $1",
        body.username,
    )
    if agent is None or not agent["password_hash"]:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if not agent["is_active"]:
        raise HTTPException(status_code=403, detail="Агент заблокирован")
    if not verify_password(body.password, agent["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_token(agent["id"], "agent")
    return {"token": token, "role": "agent", "name": agent["name"]}
