from __future__ import annotations
"""Агент-панель — эндпоинты /agent/*. JWT Bearer (role=agent)."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
                       u.cashback_balance, u.district_id, u.region_id,
                       (SELECT COUNT(*) FROM transactions t
                        WHERE t.user_id = u.id AND t.status IN ('approved','confirmed')) AS approved_tx_count
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
                   u.cashback_balance, u.district_id, u.region_id,
                   (SELECT COUNT(*) FROM transactions t
                    WHERE t.user_id = u.id AND t.status IN ('approved','confirmed')) AS approved_tx_count
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
    district_ids = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    ids = [r["district_id"] for r in district_ids]
    if ids:
        user = await db.fetchrow(
            """
            SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                   u.cashback_balance, u.telegram_id, u.language,
                   u.region_id, u.district_id, u.created_at,
                   d.name_ru AS district_name, r.name_ru AS region_name
            FROM users u
            LEFT JOIN districts d ON d.id = u.district_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE u.id = $1 AND u.district_id = ANY($2::int[])
            """,
            user_id, ids,
        )
    else:
        user = await db.fetchrow(
            """
            SELECT u.id, u.first_name, u.last_name, u.business_name, u.phone,
                   u.cashback_balance, u.telegram_id, u.language,
                   u.region_id, u.district_id, u.created_at,
                   d.name_ru AS district_name, r.name_ru AS region_name
            FROM users u
            LEFT JOIN districts d ON d.id = u.district_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE u.id = $1
            """,
            user_id,
        )
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    # История транзакций
    txns = await db.fetch(
        """SELECT id, amount, cashback_amount, status, note, created_at
           FROM transactions WHERE user_id = $1
           ORDER BY created_at DESC LIMIT 50""",
        user_id,
    )
    # История кешбэк-списаний
    spends = await db.fetch(
        """SELECT cs.id, cs.amount, cs.created_at, g.name_ru AS gift_name
           FROM cashback_spends cs
           LEFT JOIN gift_requests gr ON gr.id = cs.gift_request_id
           LEFT JOIN gifts g ON g.id = gr.gift_id
           WHERE cs.user_id = $1
           ORDER BY cs.created_at DESC LIMIT 50""",
        user_id,
    )
    # Заявки на подарки
    gift_reqs = await db.fetch(
        """SELECT gr.id, gr.status, gr.price_paid, gr.created_at, g.name_ru AS gift_name, g.image_url
           FROM gift_requests gr
           JOIN gifts g ON g.id = gr.gift_id
           WHERE gr.user_id = $1
           ORDER BY gr.created_at DESC LIMIT 20""",
        user_id,
    )

    return {
        **dict(user),
        "transactions": [dict(t) for t in txns],
        "cashback_spends": [dict(s) for s in spends],
        "gift_requests": [dict(g) for g in gift_reqs],
    }


class TxBody(BaseModel):
    user_id: int
    amount: int = Field(gt=0)
    note: str | None = None


@router.post("/transactions")
async def create_transaction(body: TxBody, bg: BackgroundTasks, agent: dict = Depends(get_current_agent)):
    user = await db.fetchrow(
        "SELECT id, district_id, first_name, last_name, business_name FROM users WHERE id = $1 AND is_active = TRUE",
        body.user_id,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    # Агент может создавать транзакции только для клиентов своих районов
    agent_districts = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    if agent_districts:  # если у агента назначены районы — проверяем
        allowed_ids = {r["district_id"] for r in agent_districts}
        if user["district_id"] not in allowed_ids:
            raise HTTPException(status_code=403, detail="Клиент не из вашего района")

    # Прогрессивный кешбэк: 1-я покупка 5%, 2-я 7%, с 3-й 10%
    approved_count = await db.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE user_id = $1 AND status IN ('approved', 'confirmed')",
        body.user_id,
    )
    if approved_count == 0:
        cashback_percent = 5
    elif approved_count == 1:
        cashback_percent = 7
    else:
        cashback_percent = 10
    cashback = round(body.amount * cashback_percent / 100)
    tx = await db.fetchrow(
        """
        INSERT INTO transactions (user_id, agent_id, amount, cashback_amount, cashback_percent, status, note)
        VALUES ($1, $2, $3, $4, $5, 'pending', $6)
        RETURNING id, user_id, amount, cashback_amount, cashback_percent, status, note, created_at
        """,
        body.user_id,
        agent["id"],
        body.amount,
        cashback,
        cashback_percent,
        body.note or "",
    )

    # Уведомить администратора о новой транзакции
    user_name = (user.get("business_name") or
                 f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or
                 f"ID {body.user_id}")

    async def _notify():
        try:
            from notifications import notify_admin_new_transaction
            await notify_admin_new_transaction(0, user_name, body.amount)
        except Exception as _e:
            import logging as _log
            _log.getLogger("scenti.agent").warning("admin notify failed: %s", _e)

    bg.add_task(_notify)
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
    "shipping":  ["confirmed", "rejected"],
    "confirmed": [],  # клиент подтверждает сам через WebApp
    "delivered": [],
    "rejected":  [],
}


@router.patch("/gift-requests/{gr_id}/status")
async def update_gift_request_status(
    gr_id: int,
    body: GrStatusBody,
    agent: dict = Depends(get_current_agent),
):
    district_ids = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    ids = [r["district_id"] for r in district_ids]
    if ids:
        gr = await db.fetchrow(
            """
            SELECT gr.id, gr.status FROM gift_requests gr
            JOIN users u ON u.id = gr.user_id
            WHERE gr.id = $1 AND u.district_id = ANY($2::int[])
            """,
            gr_id, ids,
        )
    else:
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

    # Уведомить пользователя в Telegram
    row = await db.fetchrow(
        """
        SELECT u.telegram_id, u.language, g.name_ru
        FROM gift_requests gr
        JOIN users u ON u.id = gr.user_id
        JOIN gifts g ON g.id = gr.gift_id
        WHERE gr.id = $1
        """,
        gr_id,
    )
    if row and row["telegram_id"]:
        try:
            from notifications import notify_user_gift_status
            await notify_user_gift_status(
                int(row["telegram_id"]),
                row["name_ru"],
                body.status,
                row.get("language") or "ru",
            )
        except Exception:
            pass

    return {"id": gr_id, "status": body.status}


import re as _re_stats
_D_STATS = _re_stats.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/stats")
async def my_stats(
    date_from: str = "",
    date_to: str = "",
    agent: dict = Depends(get_current_agent),
):
    # Получаем районы агента
    district_rows = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    dist_ids = [r["district_id"] for r in district_rows]

    # Если районы не назначены — агент видит 0 (нет своих клиентов)
    if not dist_ids:
        return {
            "approved_count": 0, "pending_count": 0,
            "total_amount": 0, "total_cashback": 0,
            "client_count": 0, "clients_balance": 0,
            "total_cashback_issued": 0, "total_cashback_spent": 0,
            "total_gift_requests": 0, "pending_gift_requests": 0,
            "total_gifts_value": 0,
        }

    df = date_from if date_from and _D_STATS.match(date_from) else None
    dt = date_to if date_to and _D_STATS.match(date_to) else None

    # Транзакции по agent_id (прямая привязка — совпадает с логикой admin)
    txn_params: list = [agent["id"]]
    txn_date_cond = ""
    if df and dt:
        txn_params.extend([df, dt])
        txn_date_cond = "AND t.created_at >= $2::date AND t.created_at < ($3::date + INTERVAL '1 day')"
    elif df:
        txn_params.append(df)
        txn_date_cond = "AND t.created_at >= $2::date"
    elif dt:
        txn_params.append(dt)
        txn_date_cond = "AND t.created_at < ($2::date + INTERVAL '1 day')"

    row = await db.fetchrow(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE t.status IN ('approved','confirmed')) AS approved_count,
            COUNT(*) FILTER (WHERE t.status = 'pending') AS pending_count,
            COALESCE(SUM(t.amount) FILTER (WHERE t.status IN ('approved','confirmed')), 0) AS total_amount,
            COALESCE(SUM(t.cashback_amount) FILTER (WHERE t.status IN ('approved','confirmed')), 0) AS total_cashback
        FROM transactions t
        WHERE t.agent_id = $1
          {txn_date_cond}
        """,
        *txn_params,
    )

    # Клиенты в районах агента
    client_row = await db.fetchrow(
        """
        SELECT COUNT(*) AS client_count,
               COALESCE(SUM(cashback_balance), 0) AS clients_balance
        FROM users
        WHERE is_active = TRUE AND district_id = ANY($1::int[])
        """,
        dist_ids,
    )

    # Весь кешбэк выдан за всё время клиентам в районах агента
    issued_row = await db.fetchrow(
        """
        SELECT COALESCE(SUM(t.cashback_amount) FILTER (WHERE t.status IN ('approved','confirmed')), 0) AS total_cashback_issued
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE u.district_id = ANY($1::int[])
        """,
        dist_ids,
    )

    # Только прямые оплаты кешбэком (без подарков)
    spent_row = await db.fetchrow(
        """
        SELECT COALESCE(SUM(cs.amount) FILTER (WHERE cs.gift_request_id IS NULL), 0) AS total_cashback_spent
        FROM cashback_spends cs
        JOIN users u ON u.id = cs.user_id
        WHERE u.district_id = ANY($1::int[])
        """,
        dist_ids,
    )

    # Заявки на подарки: исключаем отклонённые — совпадает с логикой admin/stats
    gift_row = await db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE gr.status != 'rejected') AS total_gift_requests,
            COUNT(*) FILTER (WHERE gr.status = 'pending') AS pending_gift_requests,
            COALESCE(SUM(gr.price_paid) FILTER (WHERE gr.status != 'rejected'), 0) AS total_gifts_value
        FROM gift_requests gr
        JOIN users u ON u.id = gr.user_id
        WHERE u.district_id = ANY($1::int[])
        """,
        dist_ids,
    )

    return {
        **dict(row),
        "client_count": int(client_row["client_count"]),
        "clients_balance": int(client_row["clients_balance"]),
        "total_cashback_issued": int(issued_row["total_cashback_issued"]),
        "total_cashback_spent": int(spent_row["total_cashback_spent"]),
        "total_gift_requests": int(gift_row["total_gift_requests"]),
        "pending_gift_requests": int(gift_row["pending_gift_requests"]),
        "total_gifts_value": int(gift_row["total_gifts_value"]),
    }


@router.get("/stats/by-district")
async def stats_by_district(
    date_from: str = "",
    date_to: str = "",
    agent: dict = Depends(get_current_agent),
):
    district_rows = await db.fetch(
        "SELECT district_id FROM agent_districts WHERE agent_id = $1", agent["id"]
    )
    dist_ids = [r["district_id"] for r in district_rows]
    if not dist_ids:
        return []

    import re as _re_d
    _D = _re_d.compile(r'^\d{4}-\d{2}-\d{2}$')
    df = date_from if date_from and _D.match(date_from) else None
    dt = date_to if date_to and _D.match(date_to) else None
    date_cond = ""
    if df: date_cond += f" AND t.created_at >= '{df}'"
    if dt: date_cond += f" AND t.created_at < '{dt}'::date + INTERVAL '1 day'"

    rows = await db.fetch(
        f"""
        SELECT
            d.id                AS district_id,
            d.name_ru           AS district_name,
            r.name_ru           AS region_name,
            COUNT(DISTINCT u.id)                                                          AS client_count,
            COUNT(t.id) FILTER (WHERE t.status IN ('approved','confirmed'){date_cond})    AS tx_count,
            COALESCE(SUM(t.amount)          FILTER (WHERE t.status IN ('approved','confirmed'){date_cond}), 0) AS total_amount,
            COALESCE(SUM(t.cashback_amount) FILTER (WHERE t.status IN ('approved','confirmed'){date_cond}), 0) AS total_cashback
        FROM districts d
        LEFT JOIN regions r ON r.id = d.region_id
        LEFT JOIN users u ON u.district_id = d.id AND u.is_active = TRUE
        LEFT JOIN transactions t ON t.user_id = u.id
        WHERE d.id = ANY($1::int[])
        GROUP BY d.id, d.name_ru, r.name_ru
        HAVING COUNT(DISTINCT u.id) > 0
        ORDER BY total_amount DESC NULLS LAST
        """,
        dist_ids,
    )
    return [dict(r) for r in rows]
