"""Агент-панель — эндпоинты /agent/*. JWT Bearer (role=agent)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database as db
from config import settings
from deps import get_current_agent

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/users")
async def search_users(search: str = "", agent: dict = Depends(get_current_agent)):
    if search:
        like = f"%{search}%"
        return await db.fetch(
            """
            SELECT id, first_name, last_name, business_name, phone, cashback_balance
            FROM users
            WHERE is_active = TRUE
              AND (first_name ILIKE $1 OR last_name ILIKE $1
                   OR business_name ILIKE $1 OR phone ILIKE $1)
            ORDER BY created_at DESC
            LIMIT 50
            """,
            like,
        )
    return await db.fetch(
        """
        SELECT id, first_name, last_name, business_name, phone, cashback_balance
        FROM users WHERE is_active = TRUE
        ORDER BY created_at DESC LIMIT 50
        """
    )


@router.get("/users/{user_id}")
async def get_user(user_id: int, agent: dict = Depends(get_current_agent)):
    user = await db.fetchrow(
        """
        SELECT id, first_name, last_name, business_name, phone,
               region_id, district_id, created_at
        FROM users WHERE id = $1
        """,
        user_id,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return user


class TxBody(BaseModel):
    user_id: int
    amount: int = Field(gt=0)
    note: str | None = None


@router.post("/transactions")
async def create_transaction(body: TxBody, agent: dict = Depends(get_current_agent)):
    user = await db.fetchrow("SELECT id FROM users WHERE id = $1 AND is_active = TRUE", body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    cashback = body.amount * settings.CASHBACK_PERCENT // 100
    tx = await db.fetchrow(
        """
        INSERT INTO transactions (user_id, agent_id, amount, cashback_amount, status, note)
        VALUES ($1, $2, $3, $4, 'pending', $5)
        RETURNING id, user_id, amount, cashback_amount, status, note, created_at
        """,
        body.user_id,
        agent["id"],
        body.amount,
        cashback,
        body.note or "",
    )
    return tx


@router.get("/transactions")
async def my_transactions(agent: dict = Depends(get_current_agent)):
    return await db.fetch(
        """
        SELECT t.id, t.user_id, t.amount, t.cashback_amount, t.status, t.note,
               t.created_at, t.confirmed_at,
               u.first_name, u.last_name, u.business_name, u.phone
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.agent_id = $1
        ORDER BY t.created_at DESC
        LIMIT 50
        """,
        agent["id"],
    )


@router.get("/stats")
async def my_stats(agent: dict = Depends(get_current_agent)):
    row = await db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'approved') AS approved_count,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'approved'), 0) AS total_amount,
            COALESCE(SUM(cashback_amount) FILTER (WHERE status = 'approved'), 0) AS total_cashback
        FROM transactions
        WHERE agent_id = $1
          AND created_at >= date_trunc('month', NOW())
        """,
        agent["id"],
    )
    return row
