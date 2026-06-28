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


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    """Кнопка запроса контакта."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку ниже",
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню с большой WebApp-кнопкой.

    Telegram принимает web_app только по HTTPS. В dev (localhost/http)
    показываем обычную кнопку, а ссылку отдаём текстом в сообщении.
    """
    if settings.webapp_is_https:
        button = KeyboardButton(
            text=BTN_OPEN_SCENTI,
            web_app=WebAppInfo(url=settings.WEBAPP_URL),
        )
    else:
        button = KeyboardButton(text=BTN_OPEN_SCENTI)

    return ReplyKeyboardMarkup(
        keyboard=[[button]],
        resize_keyboard=True,
        input_field_placeholder="Откройте ваш кабинет Scenti",
    )
