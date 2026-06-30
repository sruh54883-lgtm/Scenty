"""Reply-клавиатуры."""
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from config import settings

# Текст кнопок (для матчинга в хендлерах)
BTN_SHARE_PHONE = "📞 Поделиться номером"
BTN_OPEN_SCENTI = "🌿 Открыть Scenti"
BTN_BALANCE   = "💰 Баланс"
BTN_GIFTS     = "🎁 Подарки"
BTN_SPEND     = "💳 Оплата кешбэком"
BTN_LANG      = "🌐 Язык"

BTN_BALANCE_UZ = "💰 Balans"
BTN_GIFTS_UZ   = "🎁 Sovg'alar"
BTN_SPEND_UZ   = "💳 Keshbek bilan to'lash"
BTN_LANG_UZ    = "🌐 Til"

ALL_MENU_BTNS = {BTN_BALANCE, BTN_GIFTS, BTN_SPEND, BTN_LANG,
                 BTN_BALANCE_UZ, BTN_GIFTS_UZ, BTN_SPEND_UZ, BTN_LANG_UZ}


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def share_phone_keyboard(text: str = BTN_SHARE_PHONE) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку ниже",
    )


def main_menu_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Главное меню: 4 кнопки + WebApp кнопка внизу."""
    if lang == "uz":
        row1 = [KeyboardButton(text=BTN_BALANCE_UZ), KeyboardButton(text=BTN_GIFTS_UZ)]
        row2 = [KeyboardButton(text=BTN_SPEND_UZ),   KeyboardButton(text=BTN_LANG_UZ)]
    else:
        row1 = [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_GIFTS)]
        row2 = [KeyboardButton(text=BTN_SPEND),   KeyboardButton(text=BTN_LANG)]

    if settings.webapp_is_https:
        webapp_btn = KeyboardButton(
            text=BTN_OPEN_SCENTI,
            web_app=WebAppInfo(url=settings.WEBAPP_URL),
        )
    else:
        webapp_btn = KeyboardButton(text=BTN_OPEN_SCENTI)

    return ReplyKeyboardMarkup(
        keyboard=[row1, row2, [webapp_btn]],
        resize_keyboard=True,
        input_field_placeholder="Откройте ваш кабинет Scenti" if lang == "ru" else "Scenti kabinetingizni oching",
    )
