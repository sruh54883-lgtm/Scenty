"""Главное меню после регистрации — билингвальное (RU/UZ)."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
from config import settings
from keyboards import inline as ikb
from keyboards import reply as rkb

router = Router(name="menu")
logger = logging.getLogger(__name__)


def format_balance(amount: int | None) -> str:
    value = int(amount or 0)
    return f"{value:,}".replace(",", " ")


def menu_text(user: dict[str, Any]) -> str:
    lang = user.get("language") or "ru"
    name = user.get("first_name") or user.get("username") or ("друг" if lang == "ru" else "do'stim")
    business = user.get("business_name") or "—"
    balance = format_balance(user.get("cashback_balance"))

    if lang == "uz":
        lines = [
            f"👋 Salom, {name}!",
            "",
            f"💰 Keshbegingiz: <b>{balance} so'm</b>",
            f"🏪 Biznes: <b>{business}</b>",
            "",
            "Kabinetingizni ochish uchun tugmani bosing.",
        ]
        if not settings.webapp_is_https:
            lines += ["", f"🔗 {settings.WEBAPP_URL}"]
    else:
        lines = [
            f"👋 Привет, {name}!",
            "",
            f"💰 Ваш кешбэк: <b>{balance} сум</b>",
            f"🏪 Бизнес: <b>{business}</b>",
            "",
            "Нажмите кнопку ниже чтобы открыть ваш кабинет.",
        ]
        if not settings.webapp_is_https:
            lines += ["", f"🔗 {settings.WEBAPP_URL}"]

    return "\n".join(lines)


async def show_main_menu(message: Message, user: dict[str, Any]) -> None:
    lang = user.get("language") or "ru"
    await message.answer(
        menu_text(user),
        reply_markup=rkb.main_menu_keyboard(lang),
    )


# ── Открыть Scenti ────────────────────────────────────────────────────────────
@router.message(F.text == rkb.BTN_OPEN_SCENTI)
async def on_open_scenti(message: Message) -> None:
    if settings.webapp_is_https:
        return
    await message.answer(f"Откройте ваш кабинет по ссылке:\n{settings.WEBAPP_URL}")


# ── Баланс ────────────────────────────────────────────────────────────────────
async def _handle_balance(message: Message) -> None:
    tg_id = message.from_user.id
    user = await db.get_user_by_telegram_id(tg_id) if db.is_available() else None
    if not user:
        await message.answer("⚠️ Не удалось получить данные. Попробуйте позже.")
        return
    lang = user.get("language") or "ru"
    balance = format_balance(user.get("cashback_balance"))
    if lang == "uz":
        text = f"💰 <b>Sizning balansingiz:</b> {balance} so'm"
    else:
        text = f"💰 <b>Ваш баланс кешбэка:</b> {balance} сум"
    await message.answer(text, reply_markup=rkb.main_menu_keyboard(lang))

@router.message(F.text == rkb.BTN_BALANCE)
async def on_balance_ru(message: Message) -> None:
    await _handle_balance(message)

@router.message(F.text == rkb.BTN_BALANCE_UZ)
async def on_balance_uz(message: Message) -> None:
    await _handle_balance(message)


# ── Подарки ───────────────────────────────────────────────────────────────────
async def _handle_gifts(message: Message) -> None:
    tg_id = message.from_user.id
    user = await db.get_user_by_telegram_id(tg_id) if db.is_available() else None
    lang = (user.get("language") or "ru") if user else "ru"
    balance = format_balance(user.get("cashback_balance") if user else 0)
    if lang == "uz":
        text = (
            f"🎁 <b>Sovg'alar</b>\n\n"
            f"Mavjud balans: <b>{balance} so'm</b>\n\n"
            "Scenti ilovasini oching va sovg'ani tanlang 👇"
        )
    else:
        text = (
            f"🎁 <b>Подарки</b>\n\n"
            f"Доступный баланс: <b>{balance} сум</b>\n\n"
            "Откройте Scenti и выберите подарок 👇"
        )
    await message.answer(text, reply_markup=ikb.webapp_gifts_keyboard(lang))

@router.message(F.text == rkb.BTN_GIFTS)
async def on_gifts_ru(message: Message) -> None:
    await _handle_gifts(message)

@router.message(F.text == rkb.BTN_GIFTS_UZ)
async def on_gifts_uz(message: Message) -> None:
    await _handle_gifts(message)


# ── Оплата кешбэком ───────────────────────────────────────────────────────────
async def _handle_spend(message: Message) -> None:
    tg_id = message.from_user.id
    user = await db.get_user_by_telegram_id(tg_id) if db.is_available() else None
    lang = (user.get("language") or "ru") if user else "ru"
    balance = format_balance(user.get("cashback_balance") if user else 0)
    if lang == "uz":
        text = (
            f"💳 <b>Keshbek bilan to'lash</b>\n\n"
            f"Mavjud balans: <b>{balance} so'm</b>\n\n"
            "Ilovani oching va «To'lash» tugmasini bosing 👇"
        )
    else:
        text = (
            f"💳 <b>Оплата кешбэком</b>\n\n"
            f"Доступный баланс: <b>{balance} сум</b>\n\n"
            "Откройте приложение и нажмите «Оплатить кешбэком» 👇"
        )
    await message.answer(text, reply_markup=ikb.webapp_keyboard(lang))

@router.message(F.text == rkb.BTN_SPEND)
async def on_spend_ru(message: Message) -> None:
    await _handle_spend(message)

@router.message(F.text == rkb.BTN_SPEND_UZ)
async def on_spend_uz(message: Message) -> None:
    await _handle_spend(message)


# ── Смена языка ───────────────────────────────────────────────────────────────
async def _handle_lang(message: Message) -> None:
    await message.answer(
        "🌐 Выберите язык / Tilni tanlang:",
        reply_markup=ikb.language_keyboard(),
    )

@router.message(F.text == rkb.BTN_LANG)
async def on_lang_ru(message: Message) -> None:
    await _handle_lang(message)

@router.message(F.text == rkb.BTN_LANG_UZ)
async def on_lang_uz(message: Message) -> None:
    await _handle_lang(message)


# ── Обработка выбора языка после регистрации ──────────────────────────────────
@router.callback_query(F.data.startswith(ikb.CB_LANG_PREFIX))
async def on_change_lang(callback: CallbackQuery, state: FSMContext) -> None:
    """Меняет язык уже зарегистрированного пользователя."""
    # Если идёт регистрация — не перехватывать (start.py обработает через FSM)
    current_state = await state.get_state()
    if current_state is not None:
        return

    await callback.answer()
    lang = callback.data.split(":", 1)[1]
    if lang not in ("ru", "uz"):
        return

    tg_id = callback.from_user.id
    try:
        await db.update_language(tg_id, lang)
        user = await db.get_user_by_telegram_id(tg_id)
    except Exception:
        logger.exception("Ошибка смены языка")
        await callback.message.answer("⚠️ Ошибка. Попробуйте позже.")
        return

    await callback.message.edit_reply_markup(reply_markup=None)

    if lang == "uz":
        confirm = "✅ Til o'zgartirildi: <b>O'zbek</b>"
    else:
        confirm = "✅ Язык изменён: <b>Русский</b>"

    await callback.message.answer(confirm)

    if user:
        await show_main_menu(callback.message, user)
