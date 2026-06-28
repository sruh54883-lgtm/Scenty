"""Inline-клавиатуры."""
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# callback_data префиксы
CB_POLICY_ACCEPT = "policy:accept"
CB_POLICY_DECLINE = "policy:decline"
CB_REGION_PREFIX = "region:"
CB_DISTRICT_PREFIX = "district:"
CB_DISTRICT_BACK = "district:back"


def policy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принимаю", callback_data=CB_POLICY_ACCEPT),
                InlineKeyboardButton(text="❌ Отказываюсь", callback_data=CB_POLICY_DECLINE),
            ]
        ]
    )


def regions_keyboard(regions: list[dict[str, Any]], lang: str = "ru") -> InlineKeyboardMarkup:
    """Список регионов по 3 в ряд."""
    name_key = "name_uz" if lang == "uz" else "name_ru"
    builder = InlineKeyboardBuilder()
    for region in regions:
        builder.button(
            text=region[name_key],
            callback_data=f"{CB_REGION_PREFIX}{region['id']}",
        )
    builder.adjust(3)
    return builder.as_markup()


def districts_keyboard(districts: list[dict[str, Any]], lang: str = "ru") -> InlineKeyboardMarkup:
    """Список районов по 3 в ряд + кнопка «Назад» к регионам."""
    name_key = "name_uz" if lang == "uz" else "name_ru"
    builder = InlineKeyboardBuilder()
    for district in districts:
        builder.button(
            text=district[name_key],
            callback_data=f"{CB_DISTRICT_PREFIX}{district['id']}",
        )
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к регионам", callback_data=CB_DISTRICT_BACK))
    return builder.as_markup()
