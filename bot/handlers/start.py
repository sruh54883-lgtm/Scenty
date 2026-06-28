"""Регистрация клиента: /start и FSM-сценарий."""
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Contact, Message

import database as db
from handlers.menu import show_main_menu
from keyboards import inline as ikb
from keyboards import reply as rkb
from states import Registration

router = Router(name="start")
logger = logging.getLogger(__name__)

DB_DOWN_MSG = "⚠️ Сервис временно недоступен. Попробуйте позже."

MAX_NAME_LEN = 100
MAX_BUSINESS_LEN = 200


# --- /start ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    if not db.is_available():
        await message.answer(DB_DOWN_MSG)
        return

    telegram_id = message.from_user.id
    try:
        user = await db.get_user_by_telegram_id(telegram_id)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка чтения пользователя в /start")
        await message.answer(DB_DOWN_MSG)
        return

    # Полностью зарегистрирован → показать меню
    if user and user.get("privacy_accepted") and user.get("district_id"):
        await show_main_menu(message, user)
        return

    # Иначе — начинаем (или продолжаем) регистрацию с политики
    await _ask_policy(message, state)


async def _ask_policy(message: Message, state: FSMContext) -> None:
    try:
        policy = await db.get_privacy_policy_text("ru")
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка загрузки политики")
        await message.answer(DB_DOWN_MSG)
        return

    text = (
        "🌿 <b>Добро пожаловать в Scenti!</b>\n\n"
        "Перед регистрацией ознакомьтесь с политикой конфиденциальности:\n\n"
        f"{policy}"
    )
    await state.set_state(Registration.waiting_policy)
    await message.answer(text, reply_markup=ikb.policy_keyboard())


# --- 1. Политика ---

@router.callback_query(Registration.waiting_policy, F.data == ikb.CB_POLICY_ACCEPT)
async def policy_accept(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Registration.waiting_name)
    await callback.message.answer(
        "✅ Спасибо!\n\nВведите ваше имя:",
        reply_markup=rkb.remove(),
    )


@router.callback_query(Registration.waiting_policy, F.data == ikb.CB_POLICY_DECLINE)
async def policy_decline(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await callback.message.answer(
        "Без согласия с политикой конфиденциальности использование сервиса невозможно.\n\n"
        "Если передумаете — отправьте /start."
    )


# --- 2. Имя ---

@router.message(Registration.waiting_name, F.text)
async def process_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        await message.answer("Пожалуйста, введите корректное имя (до 100 символов):")
        return
    await state.update_data(name=name)
    await state.set_state(Registration.waiting_business)
    await message.answer("Введите название вашего бизнеса:")


# --- 3. Бизнес ---

@router.message(Registration.waiting_business, F.text)
async def process_business(message: Message, state: FSMContext) -> None:
    business = (message.text or "").strip()
    if not business or len(business) > MAX_BUSINESS_LEN:
        await message.answer("Пожалуйста, введите корректное название (до 200 символов):")
        return
    await state.update_data(business_name=business)
    await state.set_state(Registration.waiting_phone)
    await message.answer(
        "Поделитесь номером телефона, нажав кнопку ниже:",
        reply_markup=rkb.share_phone_keyboard(),
    )


# --- 4. Телефон ---

@router.message(Registration.waiting_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext) -> None:
    contact: Contact = message.contact
    # Принимаем только собственный контакт пользователя
    if contact.user_id and contact.user_id != message.from_user.id:
        await message.answer("Пожалуйста, отправьте именно ваш номер кнопкой ниже.")
        return
    phone = (contact.phone_number or "").strip()
    if not phone:
        await message.answer("Не удалось получить номер. Попробуйте ещё раз.")
        return
    await state.update_data(phone=phone)
    await _ask_region(message, state)


@router.message(Registration.waiting_phone)
async def process_phone_invalid(message: Message) -> None:
    await message.answer(
        "Пожалуйста, воспользуйтесь кнопкой «📞 Поделиться номером» ниже.",
        reply_markup=rkb.share_phone_keyboard(),
    )


async def _ask_region(message: Message, state: FSMContext) -> None:
    try:
        regions = await db.get_regions()
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка загрузки регионов")
        await message.answer(DB_DOWN_MSG)
        return
    if not regions:
        await message.answer(DB_DOWN_MSG)
        return
    await state.set_state(Registration.waiting_region)
    await message.answer(
        "Выберите ваш регион:",
        reply_markup=ikb.regions_keyboard(regions),
    )


# --- 5. Регион ---

@router.callback_query(Registration.waiting_region, F.data.startswith(ikb.CB_REGION_PREFIX))
async def process_region(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        region_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Некорректный выбор. Попробуйте /start")
        return

    try:
        districts = await db.get_districts_by_region(region_id)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка загрузки районов")
        await callback.message.answer(DB_DOWN_MSG)
        return

    await state.update_data(region_id=region_id)

    if not districts:
        # Регион без районов — сохраняем без района невозможно (district_id NOT NULL не задан,
        # но логика требует район). Просим выбрать другой регион.
        await callback.message.edit_text(
            "В этом регионе пока нет районов. Выберите другой регион:",
        )
        regions = await db.get_regions()
        await callback.message.edit_reply_markup(
            reply_markup=ikb.regions_keyboard(regions)
        )
        return

    await state.set_state(Registration.waiting_district)
    await callback.message.edit_text("Выберите ваш район:")
    await callback.message.edit_reply_markup(
        reply_markup=ikb.districts_keyboard(districts)
    )


# --- 6. Район ---

@router.callback_query(Registration.waiting_district, F.data == ikb.CB_DISTRICT_BACK)
async def district_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    regions = await db.get_regions()
    await state.set_state(Registration.waiting_region)
    await callback.message.edit_text("Выберите ваш регион:")
    await callback.message.edit_reply_markup(reply_markup=ikb.regions_keyboard(regions))


@router.callback_query(Registration.waiting_district, F.data.startswith(ikb.CB_DISTRICT_PREFIX))
async def process_district(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        district_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Некорректный выбор. Попробуйте /start")
        return

    data = await state.get_data()
    user_tg = callback.from_user

    try:
        user = await db.upsert_user(
            telegram_id=user_tg.id,
            username=user_tg.username,
            first_name=data.get("name") or user_tg.first_name,
            last_name=user_tg.last_name,
            business_name=data.get("business_name", ""),
            phone=data.get("phone", ""),
            region_id=int(data["region_id"]),
            district_id=district_id,
            language="ru",
        )
    except KeyError:
        logger.warning("Неполные данные FSM при сохранении пользователя")
        await callback.message.answer("Что-то пошло не так. Попробуйте /start")
        await state.clear()
        return
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка сохранения пользователя")
        await callback.message.answer(DB_DOWN_MSG)
        return

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ Вы успешно зарегистрированы! Добро пожаловать в Scenti! 🌿"
    )
    await show_main_menu(callback.message, user)
