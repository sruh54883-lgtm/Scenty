"""Исходящие уведомления и рассылка.

Эти функции вызывает FastAPI (например, после подтверждения транзакции
супер-админом) либо сам бот. Бот создаётся лениво как синглтон.

Пример из FastAPI:

    from bot import notifications
    await notifications.notify_user_cashback_credited(tg_id, 5000, 25000)
    ...
    await notifications.close_bot()   # на shutdown приложения
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from config import settings

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def get_bot() -> Bot:
    """Ленивый синглтон Bot. Безопасно вызывать из FastAPI и из бота."""
    global _bot
    if _bot is None:
        if not settings.BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN не задан")
        _bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def set_bot(bot: Bot) -> None:
    """Переиспользовать уже созданный Bot (например, инстанс из main.py)."""
    global _bot
    _bot = bot


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None


def _fmt(amount: int | None) -> str:
    return f"{int(amount or 0):,}".replace(",", " ")


async def _safe_send(telegram_id: int, text: str) -> bool:
    """Отправить сообщение, проглатывая ошибки Telegram (бот заблокирован и т.п.)."""
    try:
        await get_bot().send_message(telegram_id, text)
        return True
    except TelegramAPIError as exc:
        logger.warning("Не удалось отправить сообщение %s: %s", telegram_id, exc)
        return False
    except Exception:  # noqa: BLE001
        logger.exception("Неожиданная ошибка отправки %s", telegram_id)
        return False


# --- уведомления пользователю ---

async def notify_user_cashback_credited(
    telegram_id: int, amount: int, balance: int
) -> bool:
    """Кешбэк начислен."""
    text = (
        "💰 <b>Кешбэк начислен!</b>\n\n"
        f"Начислено: <b>+{_fmt(amount)} сум</b>\n"
        f"Текущий баланс: <b>{_fmt(balance)} сум</b>\n\n"
        "Откройте Scenti, чтобы использовать кешбэк. 🌿"
    )
    return await _safe_send(telegram_id, text)


async def notify_user_gift_status(
    telegram_id: int, gift_name: str, status: str
) -> bool:
    """Изменение статуса заявки на подарок."""
    titles = {
        "approved": "✅ Заявка на подарок одобрена!",
        "delivered": "🎁 Подарок выдан!",
        "rejected": "❌ Заявка на подарок отклонена",
        "pending": "⏳ Заявка на подарок принята",
    }
    title = titles.get(status, "ℹ️ Обновление по заявке на подарок")
    text = f"{title}\n\nПодарок: <b>{gift_name}</b>"
    return await _safe_send(telegram_id, text)


# --- уведомление администратору ---

async def notify_admin_new_transaction(
    admin_telegram_id: int, user_name: str, amount: int
) -> bool:
    """Новая транзакция, требующая подтверждения супер-админом."""
    target = admin_telegram_id or settings.ADMIN_TELEGRAM_ID
    if not target:
        logger.warning("ADMIN_TELEGRAM_ID не задан — уведомление админу пропущено")
        return False
    text = (
        "🔔 <b>Новая транзакция на подтверждение</b>\n\n"
        f"Клиент: <b>{user_name}</b>\n"
        f"Сумма покупки: <b>{_fmt(amount)} сум</b>\n\n"
        "Подтвердите в админ-панели."
    )
    return await _safe_send(target, text)


# --- рассылка ---

async def broadcast_message(
    message_ru: str, user_ids: Iterable[int]
) -> dict[str, int]:
    """Разослать сообщение списку пользователей.

    Возвращает {'sent': N, 'failed': M}. Соблюдает лимит ~20 сообщений/сек.
    """
    sent = 0
    failed = 0
    for telegram_id in user_ids:
        ok = await _safe_send(int(telegram_id), message_ru)
        if ok:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msg/sec, бережём лимиты Telegram
    logger.info("Рассылка завершена: отправлено=%d, ошибок=%d", sent, failed)
    return {"sent": sent, "failed": failed}
