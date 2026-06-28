"""Главное меню после регистрации."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from config import settings
from keyboards import reply as rkb

router = Router(name="menu")
logger = logging.getLogger(__name__)


def format_balance(amount: int | None) -> str:
    """1234567 -> '1 234 567'."""
    value = int(amount or 0)
    return f"{value:,}".replace(",", " ")


def menu_text(user: dict[str, Any]) -> str:
    name = user.get("first_name") or user.get("username") or "друг"
    business = user.get("business_name") or "—"
    balance = format_balance(user.get("cashback_balance"))

    lines = [
        f"👋 Привет, {name}!",
        "",
        f"💰 Ваш кешбэк: <b>{balance} сум</b>",
        f"🏪 Бизнес: <b>{business}</b>",
        "",
        "Нажмите кнопку ниже чтобы открыть ваш кабинет.",
    ]
    # В dev WebApp-кнопка по http не работает — подсказываем прямую ссылку
    if not settings.webapp_is_https:
        lines += ["", f"🔗 {settings.WEBAPP_URL}"]
    return "\n".join(lines)


async def show_main_menu(message: Message, user: dict[str, Any]) -> None:
    """Отправить приветствие с балансом и кнопкой WebApp."""
    await message.answer(
        menu_text(user),
        reply_markup=rkb.main_menu_keyboard(),
    )


@router.message(F.text == rkb.BTN_OPEN_SCENTI)
async def on_open_scenti(message: Message) -> None:
    """Нажата кнопка «Открыть Scenti» в dev-режиме (без web_app)."""
    if settings.webapp_is_https:
        # В проде кнопка сама открывает WebApp — сюда не попадаем.
        return
    await message.answer(f"Откройте ваш кабинет по ссылке:\n{settings.WEBAPP_URL}")
