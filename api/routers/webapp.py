from __future__ import annotations
"""WebApp клиент — эндпоинты /api/*. Авторизация через Telegram initData."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database as db
from deps import get_current_user

router = APIRouter(prefix="/api", tags=["webapp"])


# ---------- Профиль ----------
@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    region = None
    district = None
    if user["region_id"]:
        region = await db.fetchrow(
            "SELECT id, name_ru, name_uz FROM regions WHERE id = $1", user["region_id"]
        )
    if user["district_id"]:
        district = await db.fetchrow(
            "SELECT id, name_ru, name_uz FROM districts WHERE id = $1", user["district_id"]
        )
    return {
        "id": user["id"],
        "telegram_id": user["telegram_id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "business_name": user["business_name"],
        "phone": user["phone"],
        "cashback_balance": user["cashback_balance"],
        "language": user["language"],
        "region": region,
        "district": district,
        "privacy_accepted": user["privacy_accepted"],
    }


class LanguageBody(BaseModel):
    language: str = Field(pattern="^(ru|uz)$")


@router.put("/me/language")
async def set_language(body: LanguageBody, user: dict = Depends(get_current_user)):
    await db.execute(
        "UPDATE users SET language = $1 WHERE id = $2", body.language, user["id"]
    )
    return {"language": body.language}


# ---------- Транзакции ----------
@router.get("/transactions")
async def my_transactions(user: dict = Depends(get_current_user)):
    rows = await db.fetch(
        """
        SELECT id, amount, cashback_amount, status, note, created_at, confirmed_at
        FROM transactions
        WHERE user_id = $1
        ORDER BY created_at DESC
        """,
        user["id"],
    )
    return rows


# ---------- Трата кешбэка ----------
class SpendBody(BaseModel):
    amount: int = Field(gt=0)


@router.post("/cashback-spend")
async def cashback_spend(body: SpendBody, user: dict = Depends(get_current_user)):
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT cashback_balance FROM users WHERE id = $1 FOR UPDATE",
                user["id"],
            )
            if row["cashback_balance"] < body.amount:
                raise HTTPException(status_code=400, detail="Недостаточно средств")
            new_balance = await conn.fetchval(
                "UPDATE users SET cashback_balance = cashback_balance - $1 WHERE id = $2 RETURNING cashback_balance",
                body.amount,
                user["id"],
            )
            await conn.execute(
                "INSERT INTO cashback_spends (user_id, amount) VALUES ($1, $2)",
                user["id"],
                body.amount,
            )
    # Уведомление пользователю о списании
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import notify_user_cashback_spent
        tg_id = user.get("telegram_id")
        lang = user.get("language", "ru") or "ru"
        if tg_id:
            await notify_user_cashback_spent(int(tg_id), body.amount, new_balance, lang)
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.webapp").warning("spend notify failed: %s", _e)
    return {"spent": body.amount, "cashback_balance": new_balance}


# ---------- Подарки ----------
@router.get("/gifts")
async def list_gifts(user: dict = Depends(get_current_user)):
    return await db.fetch(
        """
        SELECT id, name_ru, name_uz, description_ru, description_uz,
               image_url, price_cashback, stock_quantity
        FROM gifts
        WHERE is_active = TRUE AND (stock_quantity IS NULL OR stock_quantity > 0)
        ORDER BY sort_order, id
        """
    )


class GiftRequestBody(BaseModel):
    gift_id: int


@router.post("/gift-requests")
async def create_gift_request(body: GiftRequestBody, user: dict = Depends(get_current_user)):
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            gift = await conn.fetchrow(
                "SELECT id, price_cashback, stock_quantity, is_active FROM gifts WHERE id = $1 FOR UPDATE",
                body.gift_id,
            )
            if gift is None or not gift["is_active"]:
                raise HTTPException(status_code=404, detail="Подарок не найден")
            if gift["stock_quantity"] is not None and gift["stock_quantity"] <= 0:
                raise HTTPException(status_code=400, detail="Подарок закончился")

            existing = await conn.fetchrow(
                """SELECT id FROM gift_requests
                   WHERE user_id=$1 AND gift_id=$2 AND status IN ('pending','approved')""",
                user["id"], body.gift_id,
            )
            if existing:
                raise HTTPException(status_code=400, detail="Активная заявка на этот подарок уже существует")

            urow = await conn.fetchrow(
                "SELECT cashback_balance FROM users WHERE id = $1 FOR UPDATE", user["id"]
            )
            if urow["cashback_balance"] < gift["price_cashback"]:
                raise HTTPException(status_code=400, detail="Недостаточно средств")

            new_balance = await conn.fetchval(
                "UPDATE users SET cashback_balance = cashback_balance - $1 WHERE id = $2 RETURNING cashback_balance",
                gift["price_cashback"],
                user["id"],
            )
            if gift["stock_quantity"] is not None:
                await conn.execute(
                    "UPDATE gifts SET stock_quantity = stock_quantity - 1 WHERE id = $1",
                    gift["id"],
                )
            req = await conn.fetchrow(
                "INSERT INTO gift_requests (user_id, gift_id, status) VALUES ($1, $2, 'pending') RETURNING id, status, created_at",
                user["id"],
                gift["id"],
            )
            await conn.execute(
                "INSERT INTO cashback_spends (user_id, amount, gift_request_id) VALUES ($1, $2, $3)",
                user["id"],
                gift["price_cashback"],
                req["id"],
            )
    return {
        "id": req["id"],
        "status": req["status"],
        "created_at": req["created_at"],
        "cashback_balance": new_balance,
    }


@router.post("/gift-requests/{req_id}/client-confirm")
async def client_confirm_gift_receipt(req_id: int, user: dict = Depends(get_current_user)):
    """Клиент подтверждает получение подарка — меняет статус confirmed → delivered."""
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT gr.id, gr.user_id FROM gift_requests gr
                   WHERE gr.id = $1 AND gr.user_id = $2 FOR UPDATE""",
                req_id, user["id"],
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Заявка не найдена")
            updated = await conn.fetchrow(
                "UPDATE gift_requests SET status='delivered' WHERE id=$1 AND status='confirmed' AND user_id=$2 RETURNING id",
                req_id, user["id"],
            )
            if updated is None:
                raise HTTPException(status_code=400, detail="Заявка не в статусе ожидания подтверждения")
    return {"status": "delivered"}


@router.get("/gift-requests")
async def my_gift_requests(user: dict = Depends(get_current_user)):
    return await db.fetch(
        """
        SELECT gr.id, gr.status, gr.admin_notes, gr.created_at,
               g.name_ru, g.name_uz, g.image_url, g.price_cashback
        FROM gift_requests gr
        JOIN gifts g ON g.id = gr.gift_id
        WHERE gr.user_id = $1
        ORDER BY gr.created_at DESC
        """,
        user["id"],
    )


# ---------- Каталог ----------
@router.get("/diffusers")
async def list_diffusers(user: dict = Depends(get_current_user)):
    return await db.fetch(
        """
        SELECT id, name_ru, name_uz, description_ru, description_uz, type,
               tag_ru, tag_uz, image_url, specs, features
        FROM diffusers
        WHERE is_active = TRUE
        ORDER BY sort_order, id
        """
    )


# ---------- Списания кешбэка ----------
@router.get("/spends")
async def my_spends(user: dict = Depends(get_current_user)):
    rows = await db.fetch(
        "SELECT id, amount, created_at FROM cashback_spends WHERE user_id = $1 ORDER BY created_at DESC",
        user["id"],
    )
    return rows


# ---------- Политика ----------
@router.get("/privacy")
async def get_privacy(user: dict = Depends(get_current_user)):
    row = await db.fetchrow(
        "SELECT content_ru, content_uz, file_url FROM privacy_policy ORDER BY id DESC LIMIT 1"
    )
    return row or {"content_ru": "", "content_uz": "", "file_url": ""}


# ---------- Регионы / районы ----------
@router.get("/regions")
async def list_regions(user: dict = Depends(get_current_user)):
    return await db.fetch("SELECT id, name_ru, name_uz, code FROM regions ORDER BY name_ru")


@router.get("/districts/{region_id}")
async def list_districts(region_id: int, user: dict = Depends(get_current_user)):
    return await db.fetch(
        "SELECT id, name_ru, name_uz, code FROM districts WHERE region_id = $1 ORDER BY name_ru",
        region_id,
    )
