from __future__ import annotations
"""Агент-панель — эндпоинты /agent/*. JWT Bearer (role=agent)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database as db
from config import settings
from deps import get_current_agent

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/me")
async def get_me(agent: dict = Depends(get_current_agent)):
    districts = await db.fetch(
        """
        SELECT d.id, d.name_ru, d.name_uz, r.id AS region_id, r.name_ru AS region_name
        FROM agent_districts ad
        JOIN districts d ON d.id = ad.district_id
        JOIN regions r ON r.id = d.region_id
        WHERE ad.agent_id = $1
        """,
        agent["id"],
    )
    return {
        "id": agent["id"],
        "name": agent["name"],
        "username": agent["username"],
        "phone": agent.get("phone", ""),
        "districts": districts,
    }


@router.get("/users")
async def search_users(
    search: str = "",
    all: bool = False,  # all=true → поиск по всем клиентам (для создания транзакций)
    agent: dict = Depends(get_current_agent),
):
    like = f"%{search}%" if search else None

    # all=true: показать всех активных клиентов (для создания транзакции агентом)
    if all:
        if like:
            return await db.fetch(
                """
                SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                       u.cashback_balance, u.district_id, u.region_id
                FROM users u
                WHERE u.is_active = TRUE
                  AND (u.first_name ILIKE $1 OR u.last_name ILIKE $1
                       OR u.business_name ILIKE $1 OR u.phone ILIKE $1)
                ORDER BY u.first_name, u.last_name LIMIT 50
                """,
                like,
            )
        return await db.fetch(
            """
            SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                   u.cashback_balance, u.district_id, u.region_id
            FROM users u WHERE u.is_active = TRUE
            ORDER BY u.created_at DESC LIMIT 100
            """
        )

    # Фильтр по районам агента (таб Клиенты)
    district_ids = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    ids = [r["district_id"] for r in district_ids]
    if not ids:
        # Нет районов — показать всех клиентов
        if like:
            return await db.fetch(
                """
                SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                       u.cashback_balance, u.district_id, u.region_id
                FROM users u WHERE u.is_active = TRUE
                  AND (u.first_name ILIKE $1 OR u.last_name ILIKE $1
                       OR u.business_name ILIKE $1 OR u.phone ILIKE $1)
                ORDER BY u.first_name LIMIT 100
                """,
                like,
            )
        return await db.fetch(
            "SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone, u.cashback_balance, u.district_id, u.region_id FROM users u WHERE u.is_active=TRUE ORDER BY u.created_at DESC LIMIT 100"
        )

    if like:
        return await db.fetch(
            """
            SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                   u.cashback_balance, u.district_id, u.region_id
            FROM users u
            WHERE u.is_active = TRUE AND u.district_id = ANY($1::int[])
              AND (u.first_name ILIKE $2 OR u.last_name ILIKE $2
                   OR u.business_name ILIKE $2 OR u.phone ILIKE $2)
            ORDER BY u.created_at DESC LIMIT 100
            """,
            ids, like,
        )
    return await db.fetch(
        """
        SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
               u.cashback_balance, u.district_id, u.region_id
        FROM users u
        WHERE u.is_active = TRUE AND u.district_id = ANY($1::int[])
        ORDER BY u.created_at DESC LIMIT 100
        """,
        ids,
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


@router.get("/gifts")
async def list_gifts(agent: dict = Depends(get_current_agent)):
    return await db.fetch(
        """
        SELECT id, name_ru, name_uz, description_ru, image_url,
               price_cashback, stock_quantity, is_active
        FROM gifts WHERE is_active = TRUE ORDER BY sort_order, id
        """
    )


@router.get("/gift-requests")
async def list_gift_requests(agent: dict = Depends(get_current_agent)):
    district_ids = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    ids = [r["district_id"] for r in district_ids]
    # Если районы не назначены — показываем все заявки
    if not ids:
        return await db.fetch(
            """
            SELECT gr.id, gr.status, gr.admin_notes, gr.created_at,
                   u.first_name, u.last_name, u.phone,
                   g.name_ru, g.image_url, g.price_cashback
            FROM gift_requests gr
            JOIN users u ON u.id = gr.user_id
            JOIN gifts g ON g.id = gr.gift_id
            ORDER BY gr.created_at DESC LIMIT 100
            """
        )
    return await db.fetch(
        """
        SELECT gr.id, gr.status, gr.admin_notes, gr.created_at,
               u.first_name, u.last_name, u.phone,
               g.name_ru, g.image_url, g.price_cashback
        FROM gift_requests gr
        JOIN users u ON u.id = gr.user_id
        JOIN gifts g ON g.id = gr.gift_id
        WHERE u.district_id = ANY($1::int[])
        ORDER BY gr.created_at DESC LIMIT 100
        """,
        ids,
    )


class GrStatusBody(BaseModel):
    status: str = Field(pattern="^(approved|shipping|confirmed|delivered|rejected)$")
    note: str | None = None


# Допустимые переходы статусов для агента
_AGENT_TRANSITIONS: dict[str, list[str]] = {
    "pending":   ["approved", "rejected"],
    "approved":  ["shipping", "rejected"],
    "shipping":  ["rejected"],
    "confirmed": ["delivered"],
    "delivered": [],
    "rejected":  [],
}


@router.patch("/gift-requests/{gr_id}/status")
async def update_gift_request_status(
    gr_id: int,
    body: GrStatusBody,
    agent: dict = Depends(get_current_agent),
):
    gr = await db.fetchrow(
        "SELECT id, status FROM gift_requests WHERE id = $1", gr_id
    )
    if gr is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    allowed = _AGENT_TRANSITIONS.get(gr["status"], [])
    if body.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Нельзя перевести из «{gr['status']}» в «{body.status}»",
        )

    await db.execute(
        "UPDATE gift_requests SET status=$1, admin_notes=COALESCE($2, admin_notes) WHERE id=$3",
        body.status,
        body.note,
        gr_id,
    )
    return {"id": gr_id, "status": body.status}


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

    client_row = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS client_count,
            COALESCE(SUM(cashback_balance), 0) AS clients_balance
        FROM users
        WHERE is_active = TRUE
          AND district_id IN (
              SELECT district_id FROM agent_districts WHERE agent_id = $1
          )
        """,
        agent["id"],
    )

    return {
        **dict(row),
        "client_count": client_row["client_count"],
        "clients_balance": client_row["clients_balance"],
    }
