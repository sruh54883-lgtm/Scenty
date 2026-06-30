"""Inline-клавиатуры."""
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


def webapp_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Кнопка открыть WebApp после регистрации."""
    btn_text = "🚀 Открыть Scenti" if lang == "ru" else "🚀 Scenti'ni ochish"
    url = settings.WEBAPP_URL
    if url.lower().startswith("https://"):
        btn = InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=url))
    else:
        btn = InlineKeyboardButton(text=btn_text, url=url)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def webapp_gifts_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Кнопка открыть WebApp на вкладке Подарки."""
    btn_text = "🎁 Подарки" if lang == "ru" else "🎁 Sovg'alar"
    url = settings.WEBAPP_URL + "#gifts"
    if url.lower().startswith("https://"):
        btn = InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=url))
    else:
        btn = InlineKeyboardButton(text=btn_text, url=url)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])

# callback_data префиксы
CB_LANG_PREFIX = "lang:"
CB_POLICY_ACCEPT = "policy:accept"
CB_POLICY_DECLINE = "policy:decline"
CB_REGION_PREFIX = "region:"
CB_DISTRICT_PREFIX = "district:"
CB_DISTRICT_BACK = "district:back"


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang:uz"),
    ]])


def policy_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    if lang == "uz":
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Qabul qilaman", callback_data=CB_POLICY_ACCEPT),
            InlineKeyboardButton(text="❌ Rad etaman", callback_data=CB_POLICY_DECLINE),
        ]])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принимаю", callback_data=CB_POLICY_ACCEPT),
        InlineKeyboardButton(text="❌ Отказываюсь", callback_data=CB_POLICY_DECLINE),
    ]])


def regions_keyboard(regions: list[dict[str, Any]], lang: str = "ru") -> InlineKeyboardMarkup:
    """Список регионов по 2 в ряд."""
    name_key = "name_uz" if lang == "uz" else "name_ru"
    builder = InlineKeyboardBuilder()
    for region in regions:
        builder.button(
            text=region[name_key],
            callback_data=f"{CB_REGION_PREFIX}{region['id']}",
        )
    builder.adjust(2)
    return builder.as_markup()


def districts_keyboard(districts: list[dict[str, Any]], lang: str = "ru") -> InlineKeyboardMarkup:
    """Список районов по 2 в ряд + кнопка «Назад» к регионам."""
    name_key = "name_uz" if lang == "uz" else "name_ru"
    builder = InlineKeyboardBuilder()
    for district in districts:
        builder.button(
            text=district[name_key],
            callback_data=f"{CB_DISTRICT_PREFIX}{district['id']}",
        )
    builder.adjust(2)
    back_text = "⬅️ Orqaga" if lang == "uz" else "⬅️ Назад к регионам"
    builder.row(InlineKeyboardButton(text=back_text, callback_data=CB_DISTRICT_BACK))
    return builder.as_markup()
