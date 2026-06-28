"""Супер-Админ панель — эндпоинты /admin/*. JWT Bearer (role=admin)."""
import json
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import database as db
from auth import hash_password
from deps import get_current_admin

router = APIRouter(prefix="/admin", tags=["admin"])


async def _audit(admin_id: int, action: str, details: dict | None = None):
    await db.execute(
        "INSERT INTO audit_log (admin_id, action, details) VALUES ($1, $2, $3)",
        admin_id,
        action,
        json.dumps(details or {}),
    )


# ============================================================ ПОЛЬЗОВАТЕЛИ
@router.get("/users")
async def list_users(
    search: str = "",
    page: int = 1,
    limit: int = 20,
    admin: dict = Depends(get_current_admin),
):
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    where = "WHERE TRUE"
    args: list = []
    if search:
        args.append(f"%{search}%")
        where += (
            " AND (first_name ILIKE $1 OR last_name ILIKE $1"
            " OR business_name ILIKE $1 OR phone ILIKE $1)"
        )

    total = await db.fetchval(f"SELECT COUNT(*) FROM users {where}", *args)
    args2 = args + [limit, offset]
    rows = await db.fetch(
        f"""
        SELECT id, telegram_id, first_name, last_name, business_name, phone,
               region_id, district_id, cashback_balance, language, is_active, created_at
        FROM users {where}
        ORDER BY created_at DESC
        LIMIT ${len(args)+1} OFFSET ${len(args)+2}
        """,
        *args2,
    )
    return {"data": rows, "total": total, "page": page, "limit": limit}


@router.get("/users/{user_id}")
async def user_detail(user_id: int, admin: dict = Depends(get_current_admin)):
    user = await db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    transactions = await db.fetch(
        """
        SELECT t.id, t.amount, t.cashback_amount, t.status, t.note,
               t.created_at, t.confirmed_at, a.name AS agent_name
        FROM transactions t
        LEFT JOIN agents a ON a.id = t.agent_id
        WHERE t.user_id = $1
        ORDER BY t.created_at DESC
        """,
        user_id,
    )
    spends = await db.fetch(
        "SELECT id, amount, created_at FROM cashback_spends WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )
    return {"user": user, "transactions": transactions, "spends": spends}


class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    business_name: str | None = None
    phone: str | None = None
    region_id: int | None = None
    district_id: int | None = None
    cashback_balance: int | None = None
    is_active: bool | None = None


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, admin: dict = Depends(get_current_admin)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")

    sets = []
    args: list = []
    for i, (k, v) in enumerate(fields.items(), start=1):
        sets.append(f"{k} = ${i}")
        args.append(v)
    args.append(user_id)
    row = await db.fetchrow(
        f"UPDATE users SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *",
        *args,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    await _audit(admin["id"], "user_update", {"user_id": user_id, "fields": list(fields.keys())})
    return row


# ============================================================ ТРАНЗАКЦИИ
@router.get("/transactions")
async def list_transactions(
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
    admin: dict = Depends(get_current_admin),
):
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    where = "WHERE TRUE"
    args: list = []
    if status:
        args.append(status)
        where += f" AND t.status = ${len(args)}"

    total = await db.fetchval(f"SELECT COUNT(*) FROM transactions t {where}", *args)
    args2 = args + [limit, offset]
    rows = await db.fetch(
        f"""
        SELECT t.id, t.user_id, t.agent_id, t.amount, t.cashback_amount, t.status,
               t.note, t.created_at, t.confirmed_at,
               u.first_name, u.last_name, u.business_name, u.phone,
               a.name AS agent_name
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN agents a ON a.id = t.agent_id
        {where}
        ORDER BY t.created_at DESC
        LIMIT ${len(args)+1} OFFSET ${len(args)+2}
        """,
        *args2,
    )
    return {"data": rows, "total": total, "page": page, "limit": limit}


@router.post("/transactions/{tx_id}/approve")
async def approve_transaction(tx_id: int, admin: dict = Depends(get_current_admin)):
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT id, user_id, cashback_amount, status FROM transactions WHERE id = $1 FOR UPDATE",
                tx_id,
            )
            if tx is None:
                raise HTTPException(status_code=404, detail="Транзакция не найдена")
            if tx["status"] != "pending":
                raise HTTPException(status_code=400, detail="Транзакция уже обработана")

            await conn.execute(
                "UPDATE transactions SET status = 'approved', confirmed_at = NOW(), confirmed_by = $1 WHERE id = $2",
                admin["id"],
                tx_id,
            )
            new_balance = await conn.fetchval(
                "UPDATE users SET cashback_balance = cashback_balance + $1 WHERE id = $2 RETURNING cashback_balance",
                tx["cashback_amount"],
                tx["user_id"],
            )
            await conn.execute(
                "INSERT INTO audit_log (admin_id, action, details) VALUES ($1, $2, $3)",
                admin["id"],
                "transaction_approve",
                json.dumps({"tx_id": tx_id, "cashback": tx["cashback_amount"], "user_id": tx["user_id"]}),
            )
    return {"status": "approved", "cashback_balance": new_balance}


class RejectBody(BaseModel):
    note: str = ""


@router.post("/transactions/{tx_id}/reject")
async def reject_transaction(tx_id: int, body: RejectBody, admin: dict = Depends(get_current_admin)):
    tx = await db.fetchrow("SELECT id, status FROM transactions WHERE id = $1", tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Транзакция не найдена")
    if tx["status"] != "pending":
        raise HTTPException(status_code=400, detail="Транзакция уже обработана")
    await db.execute(
        "UPDATE transactions SET status = 'rejected', note = $1, confirmed_at = NOW(), confirmed_by = $2 WHERE id = $3",
        body.note,
        admin["id"],
        tx_id,
    )
    await _audit(admin["id"], "transaction_reject", {"tx_id": tx_id, "note": body.note})
    return {"status": "rejected"}


# ============================================================ ПОДАРКИ
@router.get("/gifts")
async def admin_list_gifts(admin: dict = Depends(get_current_admin)):
    return await db.fetch("SELECT * FROM gifts ORDER BY sort_order, id")


class GiftBody(BaseModel):
    name_ru: str
    name_uz: str
    description_ru: str = ""
    description_uz: str = ""
    price_cashback: int = Field(gt=0)
    stock_quantity: int | None = None
    image_url: str = ""
    is_active: bool = True
    sort_order: int = 0


@router.post("/gifts")
async def create_gift(body: GiftBody, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        """
        INSERT INTO gifts (name_ru, name_uz, description_ru, description_uz,
                           price_cashback, stock_quantity, image_url, is_active, sort_order)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING *
        """,
        body.name_ru, body.name_uz, body.description_ru, body.description_uz,
        body.price_cashback, body.stock_quantity, body.image_url, body.is_active, body.sort_order,
    )
    await _audit(admin["id"], "gift_create", {"gift_id": row["id"]})
    return row


class GiftUpdate(BaseModel):
    name_ru: str | None = None
    name_uz: str | None = None
    description_ru: str | None = None
    description_uz: str | None = None
    price_cashback: int | None = None
    stock_quantity: int | None = None
    image_url: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.put("/gifts/{gift_id}")
async def update_gift(gift_id: int, body: GiftUpdate, admin: dict = Depends(get_current_admin)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    sets, args = [], []
    for i, (k, v) in enumerate(fields.items(), start=1):
        sets.append(f"{k} = ${i}")
        args.append(v)
    args.append(gift_id)
    row = await db.fetchrow(
        f"UPDATE gifts SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *", *args
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Подарок не найден")
    await _audit(admin["id"], "gift_update", {"gift_id": gift_id})
    return row


@router.delete("/gifts/{gift_id}")
async def delete_gift(gift_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gifts SET is_active = FALSE WHERE id = $1 RETURNING id", gift_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Подарок не найден")
    await _audit(admin["id"], "gift_delete", {"gift_id": gift_id})
    return {"status": "deleted"}


# ============================================================ ЗАЯВКИ НА ПОДАРКИ
@router.get("/gift-requests")
async def list_gift_requests(status: str | None = None, admin: dict = Depends(get_current_admin)):
    where = "WHERE TRUE"
    args: list = []
    if status:
        args.append(status)
        where += f" AND gr.status = ${len(args)}"
    return await db.fetch(
        f"""
        SELECT gr.id, gr.status, gr.admin_notes, gr.created_at,
               gr.user_id, gr.gift_id,
               u.first_name, u.last_name, u.business_name, u.phone,
               g.name_ru, g.name_uz, g.price_cashback, g.image_url
        FROM gift_requests gr
        JOIN users u ON u.id = gr.user_id
        JOIN gifts g ON g.id = gr.gift_id
        {where}
        ORDER BY gr.created_at DESC
        """,
        *args,
    )


@router.post("/gift-requests/{req_id}/approve")
async def approve_gift_request(req_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gift_requests SET status = 'approved' WHERE id = $1 AND status = 'pending' RETURNING id",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или уже обработана")
    await _audit(admin["id"], "gift_request_approve", {"request_id": req_id})
    return {"status": "approved"}


class GiftReqReject(BaseModel):
    admin_notes: str = ""


@router.post("/gift-requests/{req_id}/reject")
async def reject_gift_request(req_id: int, body: GiftReqReject, admin: dict = Depends(get_current_admin)):
    """Отклонить заявку и вернуть кешбэк клиенту."""
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            req = await conn.fetchrow(
                """
                SELECT gr.id, gr.user_id, gr.gift_id, gr.status, g.price_cashback, g.stock_quantity
                FROM gift_requests gr JOIN gifts g ON g.id = gr.gift_id
                WHERE gr.id = $1 FOR UPDATE
                """,
                req_id,
            )
            if req is None:
                raise HTTPException(status_code=404, detail="Заявка не найдена")
            if req["status"] not in ("pending", "approved"):
                raise HTTPException(status_code=400, detail="Заявка уже обработана")

            await conn.execute(
                "UPDATE gift_requests SET status = 'rejected', admin_notes = $1 WHERE id = $2",
                body.admin_notes, req_id,
            )
            # Возврат кешбэка
            await conn.execute(
                "UPDATE users SET cashback_balance = cashback_balance + $1 WHERE id = $2",
                req["price_cashback"], req["user_id"],
            )
            if req["stock_quantity"] is not None:
                await conn.execute(
                    "UPDATE gifts SET stock_quantity = stock_quantity + 1 WHERE id = $1",
                    req["gift_id"],
                )
            await conn.execute(
                "INSERT INTO audit_log (admin_id, action, details) VALUES ($1,$2,$3)",
                admin["id"], "gift_request_reject",
                json.dumps({"request_id": req_id, "refunded": req["price_cashback"]}),
            )
    return {"status": "rejected", "refunded": req["price_cashback"]}


@router.post("/gift-requests/{req_id}/deliver")
async def deliver_gift_request(req_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gift_requests SET status = 'delivered' WHERE id = $1 AND status = 'approved' RETURNING id",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или не подтверждена")
    await _audit(admin["id"], "gift_request_deliver", {"request_id": req_id})
    return {"status": "delivered"}


# ============================================================ ДИФФУЗОРЫ
@router.get("/diffusers")
async def admin_list_diffusers(admin: dict = Depends(get_current_admin)):
    return await db.fetch("SELECT * FROM diffusers ORDER BY sort_order, id")


class DiffuserBody(BaseModel):
    name_ru: str
    name_uz: str
    description_ru: str = ""
    description_uz: str = ""
    type: str = Field(pattern="^(device|aroma)$")
    tag_ru: str | None = None
    tag_uz: str | None = None
    image_url: str = ""
    specs: dict = {}
    features: list[str] = []
    is_active: bool = True
    sort_order: int = 0


@router.post("/diffusers")
async def create_diffuser(body: DiffuserBody, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        """
        INSERT INTO diffusers (name_ru, name_uz, description_ru, description_uz, type,
                               tag_ru, tag_uz, image_url, specs, features, is_active, sort_order)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        RETURNING *
        """,
        body.name_ru, body.name_uz, body.description_ru, body.description_uz, body.type,
        body.tag_ru, body.tag_uz, body.image_url, json.dumps(body.specs),
        body.features, body.is_active, body.sort_order,
    )
    await _audit(admin["id"], "diffuser_create", {"diffuser_id": row["id"]})
    return row


class DiffuserUpdate(BaseModel):
    name_ru: str | None = None
    name_uz: str | None = None
    description_ru: str | None = None
    description_uz: str | None = None
    type: str | None = Field(default=None, pattern="^(device|aroma)$")
    tag_ru: str | None = None
    tag_uz: str | None = None
    image_url: str | None = None
    specs: dict | None = None
    features: list[str] | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.put("/diffusers/{diffuser_id}")
async def update_diffuser(diffuser_id: int, body: DiffuserUpdate, admin: dict = Depends(get_current_admin)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    sets, args = [], []
    for i, (k, v) in enumerate(fields.items(), start=1):
        if k == "specs":
            sets.append(f"{k} = ${i}")
            args.append(json.dumps(v))
        else:
            sets.append(f"{k} = ${i}")
            args.append(v)
    args.append(diffuser_id)
    row = await db.fetchrow(
        f"UPDATE diffusers SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *", *args
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Диффузор не найден")
    await _audit(admin["id"], "diffuser_update", {"diffuser_id": diffuser_id})
    return row


@router.delete("/diffusers/{diffuser_id}")
async def delete_diffuser(diffuser_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE diffusers SET is_active = FALSE WHERE id = $1 RETURNING id", diffuser_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Диффузор не найден")
    await _audit(admin["id"], "diffuser_delete", {"diffuser_id": diffuser_id})
    return {"status": "deleted"}


# ============================================================ АГЕНТЫ
@router.get("/agents")
async def list_agents(admin: dict = Depends(get_current_admin)):
    return await db.fetch(
        """
        SELECT a.id, a.name, a.phone, a.username, a.is_active, a.created_at,
               COUNT(t.id) AS transactions_count
        FROM agents a
        LEFT JOIN transactions t ON t.agent_id = a.id
        GROUP BY a.id
        ORDER BY a.created_at DESC
        """
    )


class AgentBody(BaseModel):
    name: str
    phone: str
    username: str
    password: str
    district_ids: list[int] = []


@router.post("/agents")
async def create_agent(body: AgentBody, admin: dict = Depends(get_current_admin)):
    exists = await db.fetchval("SELECT 1 FROM agents WHERE username = $1", body.username)
    if exists:
        raise HTTPException(status_code=400, detail="Username уже занят")

    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            agent = await conn.fetchrow(
                """
                INSERT INTO agents (name, phone, username, password_hash)
                VALUES ($1,$2,$3,$4)
                RETURNING id, name, phone, username, is_active, created_at
                """,
                body.name, body.phone, body.username, hash_password(body.password),
            )
            for did in body.district_ids:
                await conn.execute(
                    "INSERT INTO agent_districts (agent_id, district_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                    agent["id"], did,
                )
    await _audit(admin["id"], "agent_create", {"agent_id": agent["id"]})
    return agent


class AgentUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    username: str | None = None
    password: str | None = None
    is_active: bool | None = None
    district_ids: list[int] | None = None


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: int, body: AgentUpdate, admin: dict = Depends(get_current_admin)):
    fields = body.model_dump(exclude_unset=True)
    district_ids = fields.pop("district_ids", None)
    password = fields.pop("password", None)

    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            sets, args = [], []
            idx = 1
            for k, v in fields.items():
                sets.append(f"{k} = ${idx}")
                args.append(v)
                idx += 1
            if password:
                sets.append(f"password_hash = ${idx}")
                args.append(hash_password(password))
                idx += 1
            if sets:
                args.append(agent_id)
                row = await conn.fetchrow(
                    f"UPDATE agents SET {', '.join(sets)} WHERE id = ${idx} RETURNING id",
                    *args,
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="Агент не найден")
            else:
                exists = await conn.fetchval("SELECT 1 FROM agents WHERE id = $1", agent_id)
                if not exists:
                    raise HTTPException(status_code=404, detail="Агент не найден")

            if district_ids is not None:
                await conn.execute("DELETE FROM agent_districts WHERE agent_id = $1", agent_id)
                for did in district_ids:
                    await conn.execute(
                        "INSERT INTO agent_districts (agent_id, district_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                        agent_id, did,
                    )
    await _audit(admin["id"], "agent_update", {"agent_id": agent_id})
    return {"status": "updated"}


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE agents SET is_active = FALSE WHERE id = $1 RETURNING id", agent_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Агент не найден")
    await _audit(admin["id"], "agent_delete", {"agent_id": agent_id})
    return {"status": "deleted"}


@router.get("/agents/{agent_id}/stats")
async def agent_stats(agent_id: int, admin: dict = Depends(get_current_admin)):
    agent = await db.fetchrow("SELECT id, name FROM agents WHERE id = $1", agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Агент не найден")
    overall = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS total_transactions,
            COUNT(*) FILTER (WHERE status = 'approved') AS approved_count,
            COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
            COUNT(*) FILTER (WHERE status = 'rejected') AS rejected_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'approved'), 0) AS total_amount,
            COALESCE(SUM(cashback_amount) FILTER (WHERE status = 'approved'), 0) AS total_cashback
        FROM transactions WHERE agent_id = $1
        """,
        agent_id,
    )
    this_month = await db.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'approved') AS approved_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'approved'), 0) AS total_amount
        FROM transactions
        WHERE agent_id = $1 AND created_at >= date_trunc('month', NOW())
        """,
        agent_id,
    )
    return {"agent": agent, "overall": overall, "this_month": this_month}


# ============================================================ РАССЫЛКА
class BroadcastBody(BaseModel):
    message_ru: str
    message_uz: str
    target: str = Field(default="all", pattern="^(all|region)$")
    region_id: int | None = None


@router.post("/broadcast")
async def create_broadcast(body: BroadcastBody, admin: dict = Depends(get_current_admin)):
    if body.target == "region" and not body.region_id:
        raise HTTPException(status_code=400, detail="Для рассылки по региону укажите region_id")

    if body.target == "region":
        recipients = await db.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_active = TRUE AND region_id = $1",
            body.region_id,
        )
    else:
        recipients = await db.fetchval("SELECT COUNT(*) FROM users WHERE is_active = TRUE")

    row = await db.fetchrow(
        """
        INSERT INTO broadcasts (message_ru, message_uz, target, region_id, sent_count)
        VALUES ($1,$2,$3,$4,$5)
        RETURNING id, target, region_id, sent_count, created_at
        """,
        body.message_ru, body.message_uz, body.target, body.region_id, recipients,
    )
    await _audit(admin["id"], "broadcast_create", {"broadcast_id": row["id"], "recipients": recipients})
    # Фактическая отправка делается воркером бота, читающим broadcasts.
    return {**row, "recipients": recipients}


# ============================================================ ПОЛИТИКА
@router.get("/privacy")
async def admin_get_privacy(admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "SELECT id, content_ru, content_uz, file_url, updated_at FROM privacy_policy ORDER BY id DESC LIMIT 1"
    )
    return row or {"content_ru": "", "content_uz": "", "file_url": ""}


class PrivacyBody(BaseModel):
    content_ru: str
    content_uz: str
    file_url: str | None = None


@router.put("/privacy")
async def admin_update_privacy(body: PrivacyBody, admin: dict = Depends(get_current_admin)):
    existing = await db.fetchrow("SELECT id FROM privacy_policy ORDER BY id DESC LIMIT 1")
    if existing:
        row = await db.fetchrow(
            "UPDATE privacy_policy SET content_ru=$1, content_uz=$2, file_url=$3, updated_at=NOW() WHERE id=$4 RETURNING *",
            body.content_ru, body.content_uz, body.file_url or "", existing["id"],
        )
    else:
        row = await db.fetchrow(
            "INSERT INTO privacy_policy (content_ru, content_uz, file_url) VALUES ($1,$2,$3) RETURNING *",
            body.content_ru, body.content_uz, body.file_url or "",
        )
    await _audit(admin["id"], "privacy_update", {})
    return row


# ============================================================ БЭКАП
@router.post("/backup")
async def backup(admin: dict = Depends(get_current_admin)):
    from config import settings

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"/tmp/scenti_backup_{ts}.sql"
    try:
        result = subprocess.run(
            ["pg_dump", "--dbname", settings.DATABASE_URL, "-f", out_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="pg_dump не установлен на сервере")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Бэкап превысил время ожидания")

    if result.returncode != 0 or not os.path.exists(out_path):
        raise HTTPException(status_code=500, detail="Не удалось создать бэкап")

    size = os.path.getsize(out_path)
    await _audit(admin["id"], "backup_create", {"path": out_path, "size": size})
    return {"download_url": out_path, "filename": os.path.basename(out_path), "size": size}


# ============================================================ СТАТИСТИКА
@router.get("/stats")
async def stats(admin: dict = Depends(get_current_admin)):
    total_users = await db.fetchval("SELECT COUNT(*) FROM users")
    pending_transactions = await db.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE status = 'pending'"
    )
    total_cashback_issued = await db.fetchval(
        "SELECT COALESCE(SUM(cashback_amount),0) FROM transactions WHERE status = 'approved'"
    )
    total_cashback_spent = await db.fetchval(
        "SELECT COALESCE(SUM(amount),0) FROM cashback_spends"
    )
    return {
        "total_users": total_users,
        "pending_transactions": pending_transactions,
        "total_cashback_issued": total_cashback_issued,
        "total_cashback_spent": total_cashback_spent,
    }
