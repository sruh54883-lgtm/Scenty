from __future__ import annotations
"""Супер-Админ панель — эндпоинты /admin/*. JWT Bearer (role=admin)."""
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import database as db
from auth import hash_password
from deps import get_current_admin

router = APIRouter(prefix="/admin", tags=["admin"])

UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "/app/uploads"))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
ALLOWED_DOC_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_DOC_SIZE = 20 * 1024 * 1024  # 20 MB


# Magic bytes для валидации реального типа файла
_IMAGE_MAGIC: dict[bytes, str] = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"RIFF": ".webp",      # RIFF????WEBP
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}
_PDF_MAGIC = b"%PDF"

_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}


def _check_image_magic(data: bytes) -> bool:
    # HEIC/HEIF: bytes[4:8] == b'ftyp', bytes[8:12] == brand
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return True
    for magic in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            if magic == b"RIFF":
                return data[8:12] == b"WEBP"
            return True
    return False


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Допустимы только JPG, PNG, WEBP, GIF")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс 5 МБ)")
    if not _check_image_magic(content):
        raise HTTPException(status_code=400, detail="Файл не является изображением")
    filename = uuid.uuid4().hex + ext
    dest = UPLOADS_DIR / filename
    dest.write_bytes(content)
    return {"url": f"/uploads/{filename}"}


@router.post("/upload-doc")
async def upload_doc(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Допустимы PDF, DOC, DOCX")
    content = await file.read()
    if len(content) > MAX_DOC_SIZE:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс 20 МБ)")
    if ext == ".pdf" and not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="Файл не является PDF")
    filename = uuid.uuid4().hex + ext
    dest = UPLOADS_DIR / filename
    dest.write_bytes(content)
    return {"url": f"/uploads/{filename}", "filename": file.filename}


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

    where = "WHERE u.is_active = TRUE"
    args: list = []
    if search:
        args.append(f"%{search}%")
        where += (
            " AND (first_name ILIKE $1 OR last_name ILIKE $1"
            " OR business_name ILIKE $1 OR phone ILIKE $1)"
        )

    total = await db.fetchval(f"SELECT COUNT(*) FROM users u {where}", *args)
    args2 = args + [limit, offset]
    rows = await db.fetch(
        f"""
        SELECT u.id, u.telegram_id, u.username,
               TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')) AS name,
               u.first_name, u.last_name,
               u.business_name AS business,
               u.phone,
               r.name_ru AS region,
               d.name_ru AS district,
               u.cashback_balance AS balance,
               u.language, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM transactions t
                WHERE t.user_id = u.id
                  AND t.status IN ('approved','confirmed')
                  AND t.created_at >= u.cashback_reset_at) AS approved_tx_count
        FROM users u
        LEFT JOIN regions r ON r.id = u.region_id
        LEFT JOIN districts d ON d.id = u.district_id
        {where}
        ORDER BY u.created_at DESC
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
    diffuser_id: int | None = None
    cashback_balance: int | None = None
    is_active: bool | None = None


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: dict = Depends(get_current_admin)):
    """Мягкое удаление: деактивирует клиента (is_active=FALSE) + уведомляет в Telegram."""
    row = await db.fetchrow(
        "UPDATE users SET is_active = FALSE WHERE id = $1 RETURNING id, telegram_id, language",
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    await _audit(admin["id"], "user_delete", {"user_id": user_id})
    if row["telegram_id"]:
        try:
            from notifications import notify_user_account_deactivated
            await notify_user_account_deactivated(row["telegram_id"], row.get("language") or "ru")
        except Exception as _e:
            import logging as _log
            _log.getLogger("scenti.admin").warning("delete_user notify failed: %s", _e)
    return {"status": "deleted"}


@router.put("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, admin: dict = Depends(get_current_admin)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    if "cashback_balance" in fields and fields["cashback_balance"] < 0:
        raise HTTPException(status_code=400, detail="Баланс не может быть отрицательным")

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
        SELECT t.id, t.user_id, t.agent_id, t.amount,
               t.cashback_amount AS cashback,
               t.status, t.note, t.created_at, t.confirmed_at,
               COALESCE(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')), 'Удалённый клиент') AS user_name,
               u.business_name, u.phone,
               a.name AS agent_name
        FROM transactions t
        LEFT JOIN users u ON u.id = t.user_id
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
            user = await conn.fetchrow(
                "SELECT telegram_id, first_name, language FROM users WHERE id = $1",
                tx["user_id"],
            )
            # user читаем ВНУТРИ транзакции — выносим до закрытия блока

    # Уведомление клиенту в Telegram
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import notify_user_cashback_credited
        if user and user["telegram_id"]:
            await notify_user_cashback_credited(
                int(user["telegram_id"]),
                int(tx["cashback_amount"]),
                int(new_balance),
                lang=user.get("language") or "ru",
            )
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.admin").warning("approve notify failed: %s", _e)

    return {"status": "approved", "cashback_balance": new_balance}


class RejectBody(BaseModel):
    note: str = ""


@router.post("/transactions/{tx_id}/reject")
async def reject_transaction(tx_id: int, body: RejectBody, admin: dict = Depends(get_current_admin)):
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT id, user_id, amount, status FROM transactions WHERE id = $1 FOR UPDATE", tx_id
            )
            if tx is None:
                raise HTTPException(status_code=404, detail="Транзакция не найдена")
            if tx["status"] != "pending":
                raise HTTPException(status_code=400, detail="Транзакция уже обработана")
            updated = await conn.fetchrow(
                """UPDATE transactions SET status = 'rejected', note = $1,
                   confirmed_at = NOW(), confirmed_by = $2
                   WHERE id = $3 AND status = 'pending' RETURNING id""",
                body.note, admin["id"], tx_id,
            )
            if updated is None:
                raise HTTPException(status_code=400, detail="Транзакция уже обработана")
            _tx_user = await conn.fetchrow(
                "SELECT telegram_id, language FROM users WHERE id = $1", tx["user_id"]
            )
    await _audit(admin["id"], "transaction_reject", {"tx_id": tx_id, "note": body.note})
    # Уведомление клиенту
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import _safe_send, _fmt
        if _tx_user and _tx_user["telegram_id"]:
            _reason = body.note or ""
            _lang = _tx_user.get("language") or "ru"
            if _lang == "uz":
                _text = (
                    f"❌ <b>Tranzaksiya rad etildi</b>\n\n"
                    f"Xarid summasi: <b>{_fmt(int(tx['amount']))} so'm</b>\n"
                    + (f"Sabab: {_reason}\n\n" if _reason else "\n")
                    + "Murojaat uchun:\n"
                    + "📞 +998 77 383 11 11\n"
                    + "💬 <a href='https://t.me/Scentioffice1'>@Scentioffice1</a>"
                )
            else:
                _text = (
                    f"❌ <b>Транзакция отклонена</b>\n\n"
                    f"Сумма покупки: <b>{_fmt(int(tx['amount']))} сум</b>\n"
                    + (f"Причина: {_reason}\n\n" if _reason else "\n")
                    + "Для уточнения свяжитесь с нами:\n"
                    + "📞 +998 77 383 11 11\n"
                    + "💬 <a href='https://t.me/Scentioffice1'>@Scentioffice1</a>"
                )
            await _safe_send(int(_tx_user["telegram_id"]), _text)
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.admin").warning("reject tx notify failed: %s", _e)
    return {"status": "rejected"}


# ============================================================ РУЧНОЕ СОЗДАНИЕ ТРАНЗАКЦИИ

class CreateTxBody(BaseModel):
    user_id: int
    amount: float = Field(..., gt=0)
    note: str = ""


@router.post("/transactions")
async def admin_create_transaction(body: CreateTxBody, admin: dict = Depends(get_current_admin)):
    amount_int = int(round(body.amount))
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT id, first_name, telegram_id FROM users WHERE id = $1",
                body.user_id,
            )
            if user is None:
                raise HTTPException(status_code=404, detail="Пользователь не найден")

            # Прогрессивный кешбэк: 1-я покупка 5%, 2-я 7%, с 3-й 10% (с момента последней регистрации)
            approved_count = await conn.fetchval(
                """SELECT COUNT(*) FROM transactions t
                   JOIN users u ON u.id = t.user_id
                   WHERE t.user_id = $1 AND t.status IN ('approved', 'confirmed')
                     AND t.created_at >= u.cashback_reset_at""",
                body.user_id,
            )
            if approved_count == 0:
                cashback_percent = 5
            elif approved_count == 1:
                cashback_percent = 7
            else:
                cashback_percent = 10
            cashback = int(round(amount_int * cashback_percent / 100))

            # Создаём как pending — кешбэк зачислится только после подтверждения
            tx_id = await conn.fetchval(
                """INSERT INTO transactions
                   (user_id, agent_id, amount, cashback_amount, cashback_percent, status, note)
                   VALUES ($1, NULL, $2, $3, $4, 'pending', $5)
                   RETURNING id""",
                body.user_id,
                amount_int,
                cashback,
                cashback_percent,
                body.note or "Ручное начисление администратором",
            )
            await conn.execute(
                "INSERT INTO audit_log (admin_id, action, details) VALUES ($1, $2, $3)",
                admin["id"],
                "transaction_manual_create",
                json.dumps({"tx_id": tx_id, "user_id": body.user_id, "amount": amount_int, "cashback": cashback, "cashback_percent": cashback_percent}),
            )

    return {"id": tx_id, "cashback_amount": cashback, "cashback_percent": cashback_percent}


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
        """UPDATE gift_requests SET status = 'approved' WHERE id = $1 AND status = 'pending'
           RETURNING id, user_id, gift_id""",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или уже обработана")
    await _audit(admin["id"], "gift_request_approve", {"request_id": req_id})
    # Уведомление клиенту
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import notify_user_gift_status
        _u = await db.fetchrow("SELECT telegram_id FROM users WHERE id=$1", row["user_id"])
        _g = await db.fetchrow("SELECT name_ru FROM gifts WHERE id=$1", row["gift_id"])
        if _u and _u["telegram_id"] and _g:
            await notify_user_gift_status(int(_u["telegram_id"]), _g["name_ru"], "approved")
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.admin").warning("approve notify failed: %s", _e)
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
            # Удаляем запись расхода — чтобы не числилось в "Потрачено"
            await conn.execute(
                "DELETE FROM cashback_spends WHERE gift_request_id = $1",
                req_id,
            )
            _gift_name_row = await conn.fetchrow("SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"])
            _new_bal = await conn.fetchval("SELECT cashback_balance FROM users WHERE id=$1", req["user_id"])
            _tg_user = await conn.fetchrow("SELECT telegram_id, language FROM users WHERE id=$1", req["user_id"])
            _gift_name = _gift_name_row["name_ru"] if _gift_name_row else "Подарок"
            # Запись возврата в историю транзакций
            await conn.execute(
                """INSERT INTO transactions (user_id, amount, cashback_amount, status, note)
                   VALUES ($1, 0, $2, 'refund', $3)""",
                req["user_id"], req["price_cashback"],
                f"Возврат кешбэка: отклонена заявка на подарок «{_gift_name}»",
            )
            await conn.execute(
                "INSERT INTO audit_log (admin_id, action, details) VALUES ($1,$2,$3)",
                admin["id"], "gift_request_reject",
                json.dumps({"request_id": req_id, "refunded": req["price_cashback"]}),
            )
    # Уведомление в Telegram
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import _safe_send, _fmt
        if _tg_user and _tg_user["telegram_id"]:
            _refund = int(req["price_cashback"] or 0)
            _balance = int(_new_bal or 0)
            _reason = body.admin_notes or ""
            _text = (
                f"❌ <b>Заявка на подарок отклонена</b>\n\n"
                f"Подарок: <b>{_gift_name}</b>\n"
                f"Возврат: <b>+{_fmt(_refund)} сум</b> → ваш баланс\n"
                f"Текущий баланс: <b>{_fmt(_balance)} сум</b>\n\n"
                + (f"Причина: {_reason}\n\n" if _reason else "")
                + "Для уточнения свяжитесь с нами:\n"
                + "📞 +998 77 383 11 11\n"
                + "💬 <a href='https://t.me/Scentioffice1'>@Scentioffice1</a>"
            )
            await _safe_send(int(_tg_user["telegram_id"]), _text)
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.admin").warning("reject_gift_request notify failed: %s", _e)
    return {"status": "rejected", "refunded": req["price_cashback"]}


@router.post("/gift-requests/{req_id}/ship")
async def ship_gift_request(req_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gift_requests SET status = 'shipping' WHERE id = $1 AND status = 'approved' RETURNING id",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или не одобрена")
    await _audit(admin["id"], "gift_request_ship", {"request_id": req_id})
    return {"status": "shipping"}


@router.post("/gift-requests/{req_id}/confirm")
async def confirm_gift_request(req_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gift_requests SET status = 'confirmed' WHERE id = $1 AND status = 'shipping' RETURNING id",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или не в статусе доставки")
    await _audit(admin["id"], "gift_request_confirm", {"request_id": req_id})
    return {"status": "confirmed"}


@router.post("/gift-requests/{req_id}/deliver")
async def deliver_gift_request(req_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE gift_requests SET status = 'delivered' WHERE id = $1 AND status IN ('approved','shipping','confirmed') RETURNING id",
        req_id,
    )
    if row is None:
        raise HTTPException(status_code=400, detail="Заявка не найдена или уже завершена")
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
async def list_agents(
    admin: dict = Depends(get_current_admin),
    date_from: str = "",
    date_to: str = "",
):
    import re as _re_ag
    _DAG = _re_ag.compile(r"^\d{4}-\d{2}-\d{2}$")
    df = date_from if date_from and _DAG.match(date_from) else None
    dt = date_to if date_to and _DAG.match(date_to) else None

    if df and dt:
        date_cond = f"t.created_at >= '{df}'::date AND t.created_at < ('{dt}'::date + INTERVAL '1 day')"
    elif df:
        date_cond = f"t.created_at >= '{df}'::date"
    elif dt:
        date_cond = f"t.created_at < ('{dt}'::date + INTERVAL '1 day')"
    else:
        date_cond = "t.created_at >= date_trunc('month', NOW())"

    return await db.fetch(
        f"""
        SELECT a.id, a.name, a.phone, a.username, a.is_active, a.created_at,
               COUNT(DISTINCT t.id) AS transactions_count,
               COUNT(DISTINCT t.id) FILTER (WHERE {date_cond} AND t.status = 'approved') AS txn_month,
               COALESCE(SUM(t.amount) FILTER (WHERE {date_cond} AND t.status = 'approved'), 0) AS sales_month,
               array_remove(array_agg(DISTINCT d.name_ru), NULL) AS districts,
               array_remove(array_agg(DISTINCT ad.district_id), NULL) AS district_ids
        FROM agents a
        LEFT JOIN transactions t ON t.agent_id = a.id
        LEFT JOIN agent_districts ad ON ad.agent_id = a.id
        LEFT JOIN districts d ON d.id = ad.district_id
        GROUP BY a.id
        ORDER BY a.created_at DESC
        """
    )


@router.delete("/reset-data")
async def reset_all_data(
    admin: dict = Depends(get_current_admin),
    include_users: bool = False,
    nuclear: bool = False,
):
    """Сброс данных. nuclear=true удаляет всё включая агентов и рассылки. НЕОБРАТИМО."""
    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM cashback_spends")
            await conn.execute("DELETE FROM gift_requests")
            await conn.execute("DELETE FROM transactions")
            if nuclear or include_users:
                await conn.execute("DELETE FROM users")
            if nuclear:
                await conn.execute("DELETE FROM agents")
                await conn.execute("DELETE FROM broadcasts")
    if nuclear:
        msg = "Полный сброс: клиенты, агенты, транзакции, рассылки удалены"
    elif include_users:
        msg = "Все данные включая клиентов очищены"
    else:
        msg = "Транзакции и заявки очищены"
    return {"status": "ok", "message": msg}


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
    daily = await db.fetch(
        """
        SELECT
            DATE(created_at AT TIME ZONE 'Asia/Tashkent') AS day,
            COUNT(*) FILTER (WHERE status IN ('approved','confirmed')) AS txn_count,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('approved','confirmed')), 0) AS total_amount
        FROM transactions
        WHERE agent_id = $1 AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 30
        """,
        agent_id,
    )
    return {"agent": agent, "overall": overall, "this_month": this_month,
            "daily": [{"day": str(r["day"]), "txn_count": r["txn_count"], "total_amount": float(r["total_amount"])} for r in daily]}


# ============================================================ РАССЫЛКА
class BroadcastBody(BaseModel):
    title: str = ""
    message_ru: str
    message_uz: str
    target: str = Field(default="all", pattern="^(all|region|language)$")
    region_id: int | None = None
    lang_filter: str | None = Field(default=None, pattern="^(ru|uz)$")
    scheduled_at: str | None = None
    parse_mode: str = "HTML"
    image_url: str = ""
    sticker_file_id: str = ""


@router.get("/broadcasts")
async def list_broadcasts(admin: dict = Depends(get_current_admin)):
    rows = await db.fetch(
        """
        SELECT b.id, b.target, b.region_id, b.sent_count, b.created_at,
               b.message_ru, b.message_uz, b.scheduled_at, b.is_sent,
               b.lang_filter, b.status, b.total_users, b.failed_count,
               b.started_at, b.completed_at, b.title, b.sticker_file_id,
               r.name_ru AS region_name
        FROM broadcasts b
        LEFT JOIN regions r ON r.id = b.region_id
        ORDER BY b.created_at DESC
        LIMIT 50
        """
    )
    return rows


def _parse_scheduled_at(raw: str | None):
    """Парсит ISO-строку в aware datetime в будущем. None — отправить сразу."""
    if not raw:
        return None
    from datetime import datetime, timezone
    s = raw.strip()
    if not s:
        return None
    try:
        # Поддержка суффикса 'Z'
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат scheduled_at (ожидается ISO datetime)")
    # Naive datetime трактуем как UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Прошедшее/текущее время — отправляем немедленно
    if dt <= datetime.now(timezone.utc):
        return None
    return dt


async def _do_broadcast_send(broadcast_id: int, users: list, message_ru: str,
                             message_uz: str, image_url: str, parse_mode: str,
                             sticker_file_id: str = ""):
    """Фоновая отправка рассылки — вызывается через BackgroundTasks."""
    import asyncio, sys, os as _os, logging as _log
    from pathlib import Path as _Path
    _bp = str(_Path(__file__).resolve().parent.parent.parent / "bot")
    if _bp not in sys.path:
        sys.path.insert(0, _bp)
    await db.execute(
        "UPDATE broadcasts SET status='sending', started_at=NOW() WHERE id=$1", broadcast_id
    )
    sent = 0
    failed = 0

    # Если image_url относительный (/uploads/...) — конвертируем в FSInputFile
    photo_input = None
    if image_url:
        if image_url.startswith("/uploads/"):
            uploads_dir = _Path(_os.environ.get("UPLOADS_DIR", "/app/uploads"))
            local_path = uploads_dir / image_url[len("/uploads/"):]
            if local_path.exists():
                from aiogram.types import FSInputFile
                photo_input = FSInputFile(str(local_path))
            else:
                _log.getLogger("scenti.broadcast").error("Image not found: %s", local_path)
                photo_input = None  # пошлём без картинки
        else:
            photo_input = image_url  # абсолютный URL — передаём как есть

    try:
        from notifications import get_bot
        bot = get_bot()
        for u in users:
            tg_id = u["telegram_id"]
            if not tg_id:
                continue
            lang = u["language"] or "ru"
            text = (message_uz or message_ru) if lang == "uz" else message_ru
            try:
                if sticker_file_id:
                    await bot.send_sticker(int(tg_id), sticker=sticker_file_id)
                    await asyncio.sleep(0.04)

                if photo_input:
                    try:
                        await bot.send_photo(
                            int(tg_id), photo=photo_input,
                            caption=text or None,
                            parse_mode=parse_mode if text else None,
                        )
                    except Exception as photo_ex:
                        _log.getLogger("scenti.broadcast").warning(
                            "send_photo failed tg_id=%s err=%s — fallback to text", tg_id, photo_ex)
                        if text:
                            await bot.send_message(int(tg_id), text, parse_mode=parse_mode)
                elif text:
                    await bot.send_message(int(tg_id), text, parse_mode=parse_mode)

                sent += 1
                await asyncio.sleep(0.05)
            except Exception as ex:
                failed += 1
                _log.getLogger("scenti.broadcast").warning("tg_id=%s err=%s", tg_id, ex)
    except Exception as e:
        _log.getLogger("scenti.api").error("Broadcast send error bid=%s: %s", broadcast_id, e)

    await db.execute(
        """UPDATE broadcasts SET status=$1, sent_count=$2, failed_count=$3,
           completed_at=NOW(), is_sent=TRUE WHERE id=$4""",
        "completed" if failed == 0 else "completed_with_errors",
        sent, failed, broadcast_id,
    )


@router.post("/broadcast")
async def create_broadcast(body: BroadcastBody, background_tasks: BackgroundTasks,
                           admin: dict = Depends(get_current_admin)):
    if body.target == "region" and not body.region_id:
        raise HTTPException(status_code=400, detail="Для рассылки по региону укажите region_id")
    if body.target == "language" and not body.lang_filter:
        raise HTTPException(status_code=400, detail="Для рассылки по языку укажите lang_filter")

    lang_filter = body.lang_filter
    scheduled_dt = _parse_scheduled_at(body.scheduled_at)

    # --- Отложенная рассылка: сохраняем, отправит фоновый воркер ---
    if scheduled_dt is not None:
        row = await db.fetchrow(
            """INSERT INTO broadcasts
                (title, message_ru, message_uz, target, region_id, lang_filter,
                 scheduled_at, parse_mode, image_url, sticker_file_id, is_sent, status, sent_count)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,FALSE,'pending',0)
               RETURNING id, target, region_id, lang_filter, scheduled_at, created_at""",
            body.title or "", body.message_ru, body.message_uz, body.target, body.region_id,
            lang_filter, scheduled_dt, body.parse_mode, body.image_url or "", body.sticker_file_id or "",
        )
        await _audit(admin["id"], "broadcast_schedule",
                     {"broadcast_id": row["id"], "scheduled_at": str(scheduled_dt)})
        return {"id": row["id"], "target": row["target"], "scheduled": True,
                "is_sent": False, "scheduled_at": row["scheduled_at"],
                "sent_count": 0, "status": "pending", "created_at": row["created_at"]}

    # --- Немедленная рассылка: собираем получателей ---
    conditions = ["is_active = TRUE", "telegram_id IS NOT NULL"]
    params: list = []
    if body.target == "region":
        params.append(body.region_id)
        conditions.append(f"region_id = ${len(params)}")
    if lang_filter:
        params.append(lang_filter)
        conditions.append(f"language = ${len(params)}")
    users = await db.fetch(
        "SELECT telegram_id, language FROM users WHERE " + " AND ".join(conditions),
        *params,
    )
    total = len(users)

    row = await db.fetchrow(
        """INSERT INTO broadcasts
            (title, message_ru, message_uz, target, region_id, lang_filter,
             parse_mode, image_url, sticker_file_id, is_sent, status, total_users, sent_count)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,FALSE,'pending',$10,0)
           RETURNING id, target, region_id, created_at""",
        body.title or "", body.message_ru, body.message_uz, body.target, body.region_id,
        lang_filter, body.parse_mode, body.image_url or "", body.sticker_file_id or "", total,
    )
    broadcast_id = row["id"]
    await _audit(admin["id"], "broadcast_create",
                 {"broadcast_id": broadcast_id, "total_users": total})

    # Запускаем отправку в фоне — HTTP отвечает сразу
    background_tasks.add_task(
        _do_broadcast_send, broadcast_id, list(users),
        body.message_ru, body.message_uz, body.image_url or "", body.parse_mode,
        body.sticker_file_id or "",
    )
    return {"id": broadcast_id, "target": body.target, "scheduled": False,
            "sent_count": 0, "total_users": total, "status": "pending",
            "created_at": row["created_at"]}


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
    import io as _io
    import json as _json
    from fastapi.responses import StreamingResponse

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"scenti_backup_{ts}.json"

    # admin_users исключён — содержит password_hash
    tables = [
        "users", "regions", "districts", "transactions", "cashback_spends",
        "gifts", "gift_requests", "agents", "agent_districts",
        "diffusers", "broadcasts", "monthly_reminder", "privacy_policy",
        "app_settings", "audit_log",
    ]

    dump: dict = {"meta": {"ts": ts, "version": "1"}, "tables": {}}

    def _serialize(val):
        if val is None:
            return None
        if isinstance(val, (int, float, bool, str)):
            return val
        return str(val)

    for table in tables:
        try:
            rows = await db.fetch(f"SELECT * FROM {table} ORDER BY id")  # noqa: S608
            dump["tables"][table] = [
                {k: _serialize(v) for k, v in dict(row).items()}
                for row in rows
            ]
        except Exception:
            dump["tables"][table] = []

    raw = _json.dumps(dump, ensure_ascii=False, indent=2).encode("utf-8")
    size = len(raw)
    await _audit(admin["id"], "backup_create", {"filename": filename, "size": size})
    return StreamingResponse(
        _io.BytesIO(raw),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================ СТАТИСТИКА
@router.get("/stats/regions")
async def stats_regions(
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_current_admin),
):
    """Статистика по регионам: клиенты + заработано кешбэка за период."""
    import re as _re
    _DATE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')
    df = date_from if date_from and _DATE.match(date_from) else None
    dt = date_to if date_to and _DATE.match(date_to) else None

    date_filter = ""
    if df:
        date_filter += f" AND t.created_at >= '{df}'"
    if dt:
        date_filter += f" AND t.created_at < '{dt}'::date + INTERVAL '1 day'"

    rows = await db.fetch(
        f"""
        SELECT r.name_ru,
               COUNT(DISTINCT u.id) AS clients,
               COALESCE(SUM(t.amount)
                   FILTER (WHERE t.status IN ('approved','confirmed'){date_filter}), 0) AS earned
        FROM regions r
        LEFT JOIN users u ON u.region_id = r.id AND u.is_active = TRUE
        LEFT JOIN transactions t ON t.user_id = u.id
        GROUP BY r.id, r.name_ru
        ORDER BY clients DESC, r.name_ru
        """
    )
    return [{"name_ru": r["name_ru"], "clients": int(r["clients"]), "earned": int(r["earned"])} for r in rows]


def _geo_date_filter(date_from, date_to):
    import re as _re
    _DATE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')
    df = date_from if date_from and _DATE.match(date_from) else None
    dt = date_to if date_to and _DATE.match(date_to) else None
    f = ""
    if df: f += f" AND t.created_at >= '{df}'"
    if dt: f += f" AND t.created_at < '{dt}'::date + INTERVAL '1 day'"
    return f


@router.get("/geo/regions")
async def geo_regions(
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_current_admin),
):
    df = _geo_date_filter(date_from, date_to)
    rows = await db.fetch(
        f"""
        SELECT r.id, r.name_ru,
               COUNT(DISTINCT u.id) AS users,
               COALESCE(SUM(t.cashback_amount)
                   FILTER (WHERE t.status IN ('approved','confirmed'){df}), 0) AS cashback,
               COUNT(DISTINCT gr.id) AS gift_requests
        FROM regions r
        LEFT JOIN users u  ON u.region_id = r.id AND u.is_active = TRUE
        LEFT JOIN transactions t ON t.user_id = u.id
        LEFT JOIN gift_requests gr ON gr.user_id = u.id
        GROUP BY r.id, r.name_ru
        ORDER BY users DESC, r.name_ru
        """
    )
    return [{"id": r["id"], "name_ru": r["name_ru"],
             "users": int(r["users"]), "balance": int(r["cashback"]),
             "gift_requests": int(r["gift_requests"])} for r in rows]


@router.get("/geo/districts")
async def geo_districts(
    region_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_current_admin),
):
    df = _geo_date_filter(date_from, date_to)
    rows = await db.fetch(
        f"""
        SELECT d.id, d.name_ru,
               COUNT(DISTINCT u.id) AS users,
               COALESCE(SUM(t.cashback_amount)
                   FILTER (WHERE t.status IN ('approved','confirmed'){df}), 0) AS cashback,
               COUNT(DISTINCT gr.id) AS gift_requests
        FROM districts d
        LEFT JOIN users u  ON u.district_id = d.id AND u.is_active = TRUE
        LEFT JOIN transactions t ON t.user_id = u.id
        LEFT JOIN gift_requests gr ON gr.user_id = u.id
        WHERE d.region_id = $1
        GROUP BY d.id, d.name_ru
        ORDER BY users DESC, d.name_ru
        """,
        region_id,
    )
    return [{"id": r["id"], "name_ru": r["name_ru"],
             "users": int(r["users"]), "balance": int(r["cashback"]),
             "gift_requests": int(r["gift_requests"])} for r in rows]


@router.get("/geo/users")
async def geo_users(
    district_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_current_admin),
):
    df = _geo_date_filter(date_from, date_to)
    rows = await db.fetch(
        f"""
        SELECT u.id, u.first_name, u.last_name, u.phone, u.cashback_balance,
               COALESCE(SUM(t.cashback_amount)
                   FILTER (WHERE t.status IN ('approved','confirmed'){df}), 0) AS cashback_period,
               COUNT(DISTINCT gr.id) AS gift_requests
        FROM users u
        LEFT JOIN transactions t ON t.user_id = u.id
        LEFT JOIN gift_requests gr ON gr.user_id = u.id
        WHERE u.district_id = $1 AND u.is_active = TRUE
        GROUP BY u.id
        ORDER BY cashback_period DESC
        """,
        district_id,
    )
    return [{"id": r["id"],
             "name": ((r["first_name"] or "") + " " + (r["last_name"] or "")).strip() or "—",
             "phone": r["phone"] or "",
             "balance": int(r["cashback_period"]),
             "current_balance": int(r["cashback_balance"]),
             "gift_requests": int(r["gift_requests"])} for r in rows]


@router.get("/stats")
async def stats(
    admin: dict = Depends(get_current_admin),
    date_from: str | None = None,
    date_to: str | None = None,
):
    import re as _re
    _DATE = _re.compile(r'^\d{4}-\d{2}-\d{2}$')
    # Validate: only YYYY-MM-DD passes — no injection possible
    df = date_from if date_from and _DATE.match(date_from) else None
    dt = date_to if date_to and _DATE.match(date_to) else None

    def _p(col: str = "created_at") -> str:
        parts = []
        if df: parts.append(f"{col} >= '{df}'")
        if dt: parts.append(f"{col} < '{dt}'::date + INTERVAL '1 day'")
        return (" AND " + " AND ".join(parts)) if parts else ""

    total_users = await db.fetchval("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
    pending_transactions = await db.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE status = 'pending'"
    )
    total_cashback_issued = await db.fetchval(
        f"SELECT COALESCE(SUM(cashback_amount),0) FROM transactions WHERE status = 'approved'{_p()}"
    )
    # Только прямые оплаты кешбэком (без обменов на подарки)
    total_cashback_spent = await db.fetchval(
        f"SELECT COALESCE(SUM(amount),0) FROM cashback_spends WHERE gift_request_id IS NULL{_p()}"
    )
    new_users_today = await db.fetchval(
        "SELECT COUNT(*) FROM users WHERE is_active = TRUE AND created_at >= CURRENT_DATE"
    )
    new_users_7d = await db.fetchval(
        "SELECT COUNT(*) FROM users WHERE is_active = TRUE AND created_at >= NOW() - INTERVAL '7 days'"
    )
    new_users_30d = await db.fetchval(
        "SELECT COUNT(*) FROM users WHERE is_active = TRUE AND created_at >= NOW() - INTERVAL '30 days'"
    )
    txns_today_count = await db.fetchval(
        f"SELECT COUNT(*) FROM transactions WHERE TRUE{_p()}"
    )
    txns_today_sum = await db.fetchval(
        f"SELECT COALESCE(SUM(amount),0) FROM transactions WHERE status='approved'{_p()}"
    )
    txns_month_sum = await db.fetchval(
        f"SELECT COALESCE(SUM(cashback_amount),0) FROM transactions WHERE status='approved'{_p()}"
    )
    active_gifts = await db.fetchval(
        "SELECT COUNT(*) FROM gifts WHERE is_active = TRUE"
    )
    pending_claims = await db.fetchval(
        "SELECT COUNT(*) FROM gift_requests WHERE status = 'pending'"
    )
    total_gift_requests = await db.fetchval(
        f"SELECT COUNT(*) FROM gift_requests WHERE status != 'rejected'{_p()}"
    )
    total_gifts_value = await db.fetchval(
        f"""SELECT COALESCE(SUM(gr.price_paid),0)
           FROM gift_requests gr
           WHERE gr.status != 'rejected'{_p('gr.created_at')}"""
    )
    # Месячный график за последние 12 месяцев (всегда, не зависит от фильтра)
    monthly_chart = await db.fetch(
        """
        SELECT TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM') AS month,
               COUNT(*) AS txn_count,
               COALESCE(SUM(amount), 0) AS total_amount,
               COALESCE(SUM(CASE WHEN status='approved' THEN cashback_amount ELSE 0 END), 0) AS cashback_sum
        FROM transactions
        WHERE created_at >= DATE_TRUNC('month', NOW()) - INTERVAL '11 months'
        GROUP BY DATE_TRUNC('month', created_at)
        ORDER BY DATE_TRUNC('month', created_at)
        """
    )
    return {
        "total_users": total_users,
        "pending_transactions": pending_transactions,
        "total_cashback_issued": total_cashback_issued,
        "total_cashback_spent": total_cashback_spent,
        "new_users_today": new_users_today,
        "new_users_7d": new_users_7d,
        "new_users_30d": new_users_30d,
        "txns_today_count": txns_today_count,
        "txns_today_sum": float(txns_today_sum),
        "txns_month_sum": float(txns_month_sum),
        "active_gifts": active_gifts,
        "pending_claims": pending_claims,
        "total_gift_requests": total_gift_requests,
        "total_gifts_value": float(total_gifts_value),
        "monthly_chart": [
            {"month": r["month"], "txn_count": r["txn_count"],
             "total_amount": float(r["total_amount"]), "cashback_sum": float(r["cashback_sum"])}
            for r in monthly_chart
        ],
    }


@router.get("/dashboard/detail")
async def dashboard_detail(
    type: str,
    date_from: str | None = None,
    date_to: str | None = None,
    admin: dict = Depends(get_current_admin),
):
    """Детализация для drawer'а на дашборде."""
    allowed = {"new_users_today","new_users_7d","new_users_30d","pending_txns",
               "approved_txns","today_txns","pending_claims","all_claims","all_users","active_gifts","all_activity",
               "cashback_spends"}
    if type not in allowed:
        raise HTTPException(status_code=400, detail="Неверный тип детализации")

    if type == "new_users_today":
        rows = await db.fetch(
            """SELECT id, TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')) AS name,
               phone, telegram_id, cashback_balance, created_at
               FROM users WHERE is_active=TRUE AND created_at>=CURRENT_DATE ORDER BY created_at DESC"""
        )
        return {"title": "Новых клиентов сегодня", "type": "users", "rows": [dict(r) for r in rows]}

    if type == "new_users_7d":
        rows = await db.fetch(
            """SELECT id, TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')) AS name,
               phone, telegram_id, cashback_balance, created_at
               FROM users WHERE is_active=TRUE AND created_at>=NOW()-INTERVAL '7 days' ORDER BY created_at DESC"""
        )
        return {"title": "Новых клиентов за 7 дней", "type": "users", "rows": [dict(r) for r in rows]}

    if type == "new_users_30d":
        rows = await db.fetch(
            """SELECT id, TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')) AS name,
               phone, telegram_id, cashback_balance, created_at
               FROM users WHERE is_active=TRUE AND created_at>=NOW()-INTERVAL '30 days' ORDER BY created_at DESC"""
        )
        return {"title": "Новых клиентов за 30 дней", "type": "users", "rows": [dict(r) for r in rows]}

    if type == "all_users":
        rows = await db.fetch(
            """SELECT id, TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')) AS name,
               phone, telegram_id, cashback_balance, created_at
               FROM users WHERE is_active=TRUE ORDER BY created_at DESC LIMIT 100"""
        )
        return {"title": "Все клиенты", "type": "users", "rows": [dict(r) for r in rows]}

    if type == "pending_txns":
        rows = await db.fetch(
            """SELECT t.id, t.amount, t.cashback_amount, t.status, t.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               a.name AS agent_name
               FROM transactions t
               JOIN users u ON u.id=t.user_id
               LEFT JOIN agents a ON a.id=t.agent_id
               WHERE t.status='pending' ORDER BY t.created_at DESC"""
        )
        return {"title": "Ожидают подтверждения", "type": "txns", "rows": [dict(r) for r in rows]}

    if type == "approved_txns":
        rows = await db.fetch(
            """SELECT t.id, t.amount, t.cashback_amount, t.status, t.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               a.name AS agent_name
               FROM transactions t
               JOIN users u ON u.id=t.user_id
               LEFT JOIN agents a ON a.id=t.agent_id
               WHERE t.status='approved' ORDER BY t.created_at DESC LIMIT 100"""
        )
        return {"title": "Выдано кешбэка", "type": "txns", "rows": [dict(r) for r in rows]}

    if type == "today_txns":
        import re as _re2
        _D = _re2.compile(r'^\d{4}-\d{2}-\d{2}$')
        df2 = date_from if date_from and _D.match(date_from) else None
        dt2 = date_to if date_to and _D.match(date_to) else None
        cond = ""
        if df2:
            cond += f" AND t.created_at >= '{df2}'"
        if dt2:
            cond += f" AND t.created_at < '{dt2}'::date + INTERVAL '1 day'"
        if not cond:
            cond = " AND t.created_at >= CURRENT_DATE"
        rows = await db.fetch(
            f"""SELECT t.id, t.amount, t.cashback_amount, t.status, t.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               a.name AS agent_name
               FROM transactions t
               JOIN users u ON u.id=t.user_id
               LEFT JOIN agents a ON a.id=t.agent_id
               WHERE TRUE{cond} ORDER BY t.created_at DESC LIMIT 300"""
        )
        return {"title": "Транзакции за период", "type": "txns", "rows": [dict(r) for r in rows]}

    if type == "pending_claims":
        rows = await db.fetch(
            """SELECT gr.id, gr.status, gr.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               g.name_ru AS gift_name, g.price_cashback AS price
               FROM gift_requests gr
               JOIN users u ON u.id=gr.user_id
               JOIN gifts g ON g.id=gr.gift_id
               WHERE gr.status='pending' ORDER BY gr.created_at DESC"""
        )
        return {"title": "Заявки на подарки (ожидают)", "type": "claims", "rows": [dict(r) for r in rows]}

    if type == "all_claims":
        import re as _re3
        _D = _re3.compile(r'^\d{4}-\d{2}-\d{2}$')
        df3 = date_from if date_from and _D.match(date_from) else None
        dt3 = date_to if date_to and _D.match(date_to) else None
        cond = ""
        if df3:
            cond += f" AND gr.created_at >= '{df3}'"
        if dt3:
            cond += f" AND gr.created_at < '{dt3}'::date + INTERVAL '1 day'"
        rows = await db.fetch(
            f"""SELECT gr.id, gr.status, gr.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               g.name_ru AS gift_name, gr.price_paid AS price
               FROM gift_requests gr
               JOIN users u ON u.id=gr.user_id
               JOIN gifts g ON g.id=gr.gift_id
               WHERE TRUE{cond} ORDER BY gr.created_at DESC LIMIT 200"""
        )
        return {"title": "Все заявки на подарки", "type": "claims", "rows": [dict(r) for r in rows]}

    if type == "active_gifts":
        rows = await db.fetch(
            "SELECT id, name_ru, name_uz, price_cashback, stock_quantity AS stock, created_at FROM gifts WHERE is_active=TRUE ORDER BY created_at DESC"
        )
        return {"title": "Активные подарки", "type": "gifts", "rows": [dict(r) for r in rows]}

    if type == "cashback_spends":
        import re as _re4
        _D = _re4.compile(r'^\d{4}-\d{2}-\d{2}$')
        df4 = date_from if date_from and _D.match(date_from) else None
        dt4 = date_to if date_to and _D.match(date_to) else None
        cond = ""
        if df4:
            cond += f" AND cs.created_at >= '{df4}'"
        if dt4:
            cond += f" AND cs.created_at < '{dt4}'::date + INTERVAL '1 day'"
        rows = await db.fetch(
            f"""SELECT cs.id, cs.amount, cs.created_at, cs.gift_request_id,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name
               FROM cashback_spends cs
               JOIN users u ON u.id=cs.user_id
               WHERE cs.gift_request_id IS NULL{cond} ORDER BY cs.created_at DESC LIMIT 300"""
        )
        return {"title": "Потраченный кешбэк", "type": "cashback_spends", "rows": [dict(r) for r in rows]}

    if type == "all_activity":
        # Покупки (транзакции)
        txns = await db.fetch(
            """SELECT t.id, t.amount, t.cashback_amount, t.status, t.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               a.name AS agent_name
               FROM transactions t
               JOIN users u ON u.id=t.user_id
               LEFT JOIN agents a ON a.id=t.agent_id
               ORDER BY t.created_at DESC LIMIT 200"""
        )
        # Заявки на подарки
        claims = await db.fetch(
            """SELECT gr.id, gr.status, gr.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name,
               g.name_ru AS gift_name, g.price_cashback AS price
               FROM gift_requests gr
               JOIN users u ON u.id=gr.user_id
               JOIN gifts g ON g.id=gr.gift_id
               ORDER BY gr.created_at DESC LIMIT 200"""
        )
        # Оплаты кешбэком
        spends = await db.fetch(
            """SELECT cs.id, cs.amount, cs.created_at,
               TRIM(COALESCE(u.first_name,'')||' '||COALESCE(u.last_name,'')) AS user_name
               FROM cashback_spends cs
               JOIN users u ON u.id=cs.user_id
               ORDER BY cs.created_at DESC LIMIT 200"""
        )
        return {
            "title": "Вся активность",
            "type": "all_activity",
            "txns": [dict(r) for r in txns],
            "claims": [dict(r) for r in claims],
            "spends": [dict(r) for r in spends],
        }


# ============================================================ CLAIMS (alias gift-requests)
@router.get("/claims")
async def list_claims(status: str | None = None, admin: dict = Depends(get_current_admin)):
    where = "WHERE TRUE"
    args: list = []
    if status:
        args.append(status)
        where += f" AND gr.status = ${len(args)}"
    rows = await db.fetch(
        f"""
        SELECT gr.id, gr.status, gr.admin_notes, gr.created_at, gr.user_id, gr.gift_id,
               TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')) AS user_name,
               g.name_ru AS gift_name,
               g.price_cashback AS price,
               g.image_url
        FROM gift_requests gr
        JOIN users u ON u.id = gr.user_id
        JOIN gifts g ON g.id = gr.gift_id
        {where}
        ORDER BY gr.created_at DESC
        """,
        *args,
    )
    return rows


class ClaimStatusBody(BaseModel):
    status: str
    reason: str = ""


@router.put("/claims/{req_id}")
async def update_claim(req_id: int, body: ClaimStatusBody, admin: dict = Depends(get_current_admin)):
    status_map = {
        "approved": "approved",
        "shipping": "shipping",
        "confirmed": "confirmed",
        "issued": "delivered",
        "rejected": "rejected",
    }
    new_status = status_map.get(body.status)
    if not new_status:
        raise HTTPException(status_code=400, detail="Неверный статус")

    async with db.get_pool().acquire() as conn:
        async with conn.transaction():
            req = await conn.fetchrow(
                """SELECT gr.id, gr.user_id, gr.gift_id, gr.status, g.price_cashback, g.stock_quantity
                FROM gift_requests gr JOIN gifts g ON g.id = gr.gift_id
                WHERE gr.id = $1 FOR UPDATE""",
                req_id,
            )
            if req is None:
                raise HTTPException(status_code=404, detail="Заявка не найдена")

            if new_status == "rejected":
                if req["status"] not in ("pending", "approved"):
                    raise HTTPException(status_code=400, detail="Заявка уже обработана")
                await conn.execute(
                    "UPDATE gift_requests SET status='rejected', admin_notes=$1 WHERE id=$2",
                    body.reason, req_id,
                )
                await conn.execute(
                    "UPDATE users SET cashback_balance = cashback_balance + $1 WHERE id = $2",
                    req["price_cashback"], req["user_id"],
                )
                # Удаляем запись расхода — чтобы не числилось в "Потрачено"
                await conn.execute(
                    "DELETE FROM cashback_spends WHERE gift_request_id = $1",
                    req_id,
                )
                # Восстанавливаем остаток на складе
                if req["stock_quantity"] is not None:
                    await conn.execute(
                        "UPDATE gifts SET stock_quantity = stock_quantity + 1 WHERE id = $1",
                        req["gift_id"],
                    )
                new_bal = await conn.fetchval(
                    "SELECT cashback_balance FROM users WHERE id=$1", req["user_id"]
                )
                user_row = await conn.fetchrow(
                    "SELECT telegram_id FROM users WHERE id=$1", req["user_id"]
                )
                gift_row = await conn.fetchrow(
                    "SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"]
                )
                # Запись возврата в историю транзакций
                _gn = gift_row["name_ru"] if gift_row else "Подарок"
                await conn.execute(
                    """INSERT INTO transactions (user_id, amount, cashback_amount, status, note)
                       VALUES ($1, 0, $2, 'refund', $3)""",
                    req["user_id"], req["price_cashback"],
                    f"Возврат кешбэка: отклонена заявка на подарок «{_gn}»",
                )
            elif new_status == "shipping":
                # approved → shipping (передано службе доставки)
                updated = await conn.fetchrow(
                    "UPDATE gift_requests SET status='shipping' WHERE id=$1 AND status='approved' RETURNING id",
                    req_id,
                )
                if updated is None:
                    raise HTTPException(status_code=400, detail="Заявка не в статусе 'approved'")
                user_row = await conn.fetchrow(
                    "SELECT telegram_id, language FROM users WHERE id=$1", req["user_id"]
                )
                gift_row = await conn.fetchrow(
                    "SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"]
                )
            elif new_status == "confirmed":
                # shipping → confirmed (доставлено, ждём подтверждения клиента)
                updated = await conn.fetchrow(
                    "UPDATE gift_requests SET status='confirmed' WHERE id=$1 AND status='shipping' RETURNING id",
                    req_id,
                )
                if updated is None:
                    raise HTTPException(status_code=400, detail="Заявка не в статусе 'shipping'")
                user_row = await conn.fetchrow(
                    "SELECT telegram_id, language FROM users WHERE id=$1", req["user_id"]
                )
                gift_row = await conn.fetchrow(
                    "SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"]
                )
            elif new_status == "delivered":
                updated = await conn.fetchrow(
                    "UPDATE gift_requests SET status='delivered' WHERE id=$1 AND status='confirmed' RETURNING id",
                    req_id,
                )
                if updated is None:
                    raise HTTPException(status_code=400, detail="Заявка не в статусе 'confirmed'")
                user_row = await conn.fetchrow(
                    "SELECT telegram_id, language FROM users WHERE id=$1", req["user_id"]
                )
                gift_row = await conn.fetchrow(
                    "SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"]
                )
            else:
                # approved — только из pending
                updated = await conn.fetchrow(
                    "UPDATE gift_requests SET status=$1 WHERE id=$2 AND status='pending' RETURNING id",
                    new_status, req_id,
                )
                if updated is None:
                    raise HTTPException(status_code=400, detail="Заявка уже обработана")
                user_row = await conn.fetchrow(
                    "SELECT telegram_id, language FROM users WHERE id=$1", req["user_id"]
                )
                gift_row = await conn.fetchrow(
                    "SELECT name_ru FROM gifts WHERE id=$1", req["gift_id"]
                )
    await _audit(admin["id"], f"claim_{new_status}", {"request_id": req_id})
    # Telegram-уведомление для всех статусов
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _bot_path = str(_Path(__file__).resolve().parent.parent.parent / "bot")
        if _bot_path not in _sys.path:
            _sys.path.insert(0, _bot_path)
        from notifications import _safe_send, _fmt, notify_user_gift_status
        if user_row and user_row["telegram_id"]:
            _tg = int(user_row["telegram_id"])
            _lang = user_row.get("language") or "ru"
            _gift_name = gift_row["name_ru"] if gift_row else "Подарок"
            if new_status == "rejected":
                _refund = int(req["price_cashback"] or 0)
                _balance = int(new_bal or 0)
                _reason = body.reason or ""
                if _lang == "uz":
                    _text = (
                        f"❌ <b>Sovg'a so'rovi rad etildi</b>\n\n"
                        f"Sovg'a: <b>{_gift_name}</b>\n"
                        f"Qaytarildi: <b>+{_fmt(_refund)} so'm</b> → balansingiz\n"
                        f"Joriy balans: <b>{_fmt(_balance)} so'm</b>\n\n"
                        + (f"Sabab: {_reason}\n\n" if _reason else "")
                        + "Bog'lanish uchun:\n"
                        + "📞 +998 77 383 11 11\n"
                        + "💬 <a href='https://t.me/Scentioffice1'>@Scentioffice1</a>"
                    )
                else:
                    _text = (
                        f"❌ <b>Заявка на подарок отклонена</b>\n\n"
                        f"Подарок: <b>{_gift_name}</b>\n"
                        f"Возврат: <b>+{_fmt(_refund)} сум</b> → ваш баланс\n"
                        f"Текущий баланс: <b>{_fmt(_balance)} сум</b>\n\n"
                        + (f"Причина: {_reason}\n\n" if _reason else "")
                        + "Для уточнения свяжитесь с нами:\n"
                        + "📞 +998 77 383 11 11\n"
                        + "💬 <a href='https://t.me/Scentioffice1'>@Scentioffice1</a>"
                    )
                await _safe_send(_tg, _text)
            elif new_status == "approved":
                await notify_user_gift_status(_tg, _gift_name, "approved", _lang)
            elif new_status == "shipping":
                await notify_user_gift_status(_tg, _gift_name, "shipping", _lang)
            elif new_status == "confirmed":
                await notify_user_gift_status(_tg, _gift_name, "confirmed", _lang)
            elif new_status == "delivered":
                await notify_user_gift_status(_tg, _gift_name, "delivered", _lang)
    except Exception as _e:
        import logging as _log
        _log.getLogger("scenti.admin").warning("claim notify failed: %s", _e)
    return {"status": new_status}


# ============================================================ CATALOG (alias diffusers)
@router.get("/catalog")
async def list_catalog(admin: dict = Depends(get_current_admin)):
    return await db.fetch("SELECT * FROM diffusers ORDER BY sort_order, id")


class CatalogBody(BaseModel):
    name_ru: str = ""
    name_uz: str = ""
    description_ru: str = ""
    description_uz: str = ""
    type: str = Field(default="device", pattern="^(device|aroma)$")
    tag_ru: str | None = None
    tag_uz: str | None = None
    image_url: str = ""
    sort_order: int = 0


@router.post("/catalog")
async def create_catalog_item(body: CatalogBody, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        """INSERT INTO diffusers (name_ru, name_uz, description_ru, description_uz, type,
           tag_ru, tag_uz, image_url, sort_order)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
        body.name_ru, body.name_uz,
        body.description_ru, body.description_uz,
        body.type,
        body.tag_ru, body.tag_uz,
        body.image_url, body.sort_order,
    )
    return row


@router.delete("/catalog/{item_id}")
async def delete_catalog_item(item_id: int, admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow(
        "UPDATE diffusers SET is_active = FALSE WHERE id = $1 RETURNING id", item_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    await _audit(admin["id"], "catalog_delete", {"item_id": item_id})
    return {"status": "deleted"}


@router.put("/catalog/{item_id}")
async def update_catalog_item(item_id: int, body: dict, admin: dict = Depends(get_current_admin)):
    allowed = {"name_ru","name_uz","description_ru","description_uz","type","tag_ru","tag_uz","image_url","sort_order","is_active"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if "type" in fields and fields["type"] not in ("device", "aroma", "scent"):
        raise HTTPException(status_code=400, detail="Недопустимый тип: только device, aroma, scent")
    if not fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    sets = [f"{k} = ${i+1}" for i, k in enumerate(fields)]
    vals = list(fields.values()) + [item_id]
    row = await db.fetchrow(
        f"UPDATE diffusers SET {', '.join(sets)} WHERE id = ${len(vals)} RETURNING id",
        *vals,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return {"status": "updated"}


# ============================================================ ЕЖЕМЕСЯЧНОЕ НАПОМИНАНИЕ
@router.get("/reminder")
async def get_reminder(admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow("SELECT * FROM monthly_reminder ORDER BY id DESC LIMIT 1")
    if not row:
        return {"text_ru": "", "text_uz": "", "active": True}
    return {"text_ru": row["message_ru"], "text_uz": row["message_uz"], "active": row["is_active"]}


class ReminderBody(BaseModel):
    text_ru: str = ""
    text_uz: str = ""
    active: bool = True


@router.put("/reminder")
async def update_reminder(body: ReminderBody, admin: dict = Depends(get_current_admin)):
    existing = await db.fetchrow("SELECT id FROM monthly_reminder ORDER BY id DESC LIMIT 1")
    if existing:
        await db.execute(
            "UPDATE monthly_reminder SET message_ru=$1, message_uz=$2, is_active=$3 WHERE id=$4",
            body.text_ru, body.text_uz, body.active, existing["id"],
        )
    else:
        await db.execute(
            "INSERT INTO monthly_reminder (message_ru, message_uz, is_active) VALUES ($1,$2,$3)",
            body.text_ru, body.text_uz, body.active,
        )
    await _audit(admin["id"], "reminder_update", {})
    return {"status": "updated"}


# ============================================================ ПОЛИТИКА (alias)
@router.get("/policy")
async def get_policy(admin: dict = Depends(get_current_admin)):
    row = await db.fetchrow("SELECT * FROM privacy_policy ORDER BY id DESC LIMIT 1")
    if not row:
        return {"ru": "", "uz": "", "pdf": ""}
    return {"ru": row["content_ru"], "uz": row["content_uz"], "pdf": row.get("file_url", "")}


class PolicyBody(BaseModel):
    ru: str = ""
    uz: str = ""
    pdf: str = ""


@router.put("/policy")
async def update_policy(body: PolicyBody, admin: dict = Depends(get_current_admin)):
    existing = await db.fetchrow("SELECT id FROM privacy_policy ORDER BY id DESC LIMIT 1")
    if existing:
        await db.execute(
            "UPDATE privacy_policy SET content_ru=$1, content_uz=$2, file_url=$3, updated_at=NOW() WHERE id=$4",
            body.ru, body.uz, body.pdf, existing["id"],
        )
    else:
        await db.execute(
            "INSERT INTO privacy_policy (content_ru, content_uz, file_url) VALUES ($1,$2,$3)",
            body.ru, body.uz, body.pdf,
        )
    await _audit(admin["id"], "policy_update", {})
    return {"status": "updated"}


# ============================================================ АУДИТ ЛОГ
@router.get("/audit")
async def get_audit(limit: int = 50, admin: dict = Depends(get_current_admin)):
    rows = await db.fetch(
        """SELECT al.id, al.action, al.details, al.ip_address, al.created_at,
                  au.username AS admin
           FROM audit_log al
           LEFT JOIN admin_users au ON au.id = al.admin_id
           ORDER BY al.created_at DESC
           LIMIT $1""",
        min(limit, 200),
    )
    return rows


# ============================================================ БЭКАПЫ (список)
# ============================================================ РЕГИОНЫ / РАЙОНЫ
@router.get("/regions")
async def admin_list_regions(admin: dict = Depends(get_current_admin)):
    regions = await db.fetch("SELECT id, name_ru, name_uz FROM regions ORDER BY name_ru")
    districts_all = await db.fetch(
        "SELECT id, region_id, name_ru, name_uz FROM districts ORDER BY name_ru"
    )
    dist_map: dict[int, list] = {}
    for d in districts_all:
        dist_map.setdefault(d["region_id"], []).append(dict(d))
    return [{**dict(r), "districts": dist_map.get(r["id"], [])} for r in regions]


@router.get("/backups")
async def list_backups(admin: dict = Depends(get_current_admin)):
    import glob
    files = sorted(glob.glob("/tmp/scenti_backup_*.json") + glob.glob("/tmp/scenti_backup_*.sql"), reverse=True)[:10]
    result = []
    for f in files:
        try:
            size_bytes = os.path.getsize(f)
            size = f"{size_bytes // 1024} KB"
            name = os.path.basename(f)
            result.append({"id": name, "name": name, "size": size,
                           "created_at": datetime.fromtimestamp(os.path.getmtime(f)).isoformat()})
        except Exception:
            pass
    return result


# ============================================================ НАСТРОЙКИ
@router.get("/settings")
async def get_settings(admin: dict = Depends(get_current_admin)):
    rows = await db.fetch("SELECT key, value FROM app_settings ORDER BY key")
    return {r["key"]: r["value"] for r in rows}


class SettingsBody(BaseModel):
    contact_phone: str = ""
    contact_phone_display: str = ""
    contact_tg: str = ""
    youtube_url: str = ""
    instagram_url: str = ""


@router.put("/settings")
async def update_settings(body: SettingsBody, admin: dict = Depends(get_current_admin)):
    data = {
        "contact_phone": body.contact_phone.strip(),
        "contact_phone_display": body.contact_phone_display.strip(),
        "contact_tg": body.contact_tg.strip().lstrip("@"),
        "youtube_url": body.youtube_url.strip(),
        "instagram_url": body.instagram_url.strip(),
    }
    for key, value in data.items():
        await db.execute(
            "INSERT INTO app_settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=$2",
            key, value,
        )
    await _audit(admin["id"], "settings_update", data)
    return {"ok": True}
