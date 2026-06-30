"""Локальный запуск бота с SQLite (без PostgreSQL)."""
import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Contact,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

sys.path.insert(0, os.path.dirname(__file__))
import db_local as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8181")


class Reg(StatesGroup):
    policy = State()
    name = State()
    business = State()
    phone = State()
    region = State()
    district = State()


router = Router()


def fmt_balance(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def regions_kb(regions: list) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for i, r in enumerate(regions):
        row.append(InlineKeyboardButton(text=r["name_ru"], callback_data=f"region:{r['id']}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def districts_kb(districts: list) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for d in districts:
        row.append(InlineKeyboardButton(text=d["name_ru"], callback_data=f"district:{d['id']}:{d['name_ru']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="← Назад к регионам", callback_data="back_regions")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_kb() -> ReplyKeyboardMarkup:
    is_https = WEBAPP_URL.startswith("https://")
    if is_https:
        open_btn = KeyboardButton(text="🌿 Открыть Scenti", web_app=WebAppInfo(url=WEBAPP_URL))
    else:
        open_btn = KeyboardButton(text="🌿 Открыть Scenti")
    return ReplyKeyboardMarkup(
        keyboard=[
            [open_btn],
            [KeyboardButton(text="💳 Оплата кешбэком"), KeyboardButton(text="💰 Мой баланс")],
            [KeyboardButton(text="🎁 Подарки"), KeyboardButton(text="🌐 Язык")],
        ],
        resize_keyboard=True,
    )


def menu_inline_kb() -> InlineKeyboardMarkup:
    is_https = WEBAPP_URL.startswith("https://")
    if is_https:
        open_btn = InlineKeyboardButton(text="🌿 Открыть Scenti", web_app=WebAppInfo(url=WEBAPP_URL))
    else:
        open_btn = InlineKeyboardButton(text="🌿 Открыть Scenti", url=WEBAPP_URL)
    return InlineKeyboardMarkup(inline_keyboard=[
        [open_btn],
        [
            InlineKeyboardButton(text="💳 Оплата кешбэком", callback_data="mi:pay"),
            InlineKeyboardButton(text="💰 Мой баланс", callback_data="mi:balance"),
        ],
        [
            InlineKeyboardButton(text="🎁 Подарки", callback_data="mi:gifts"),
            InlineKeyboardButton(text="🌐 Язык", callback_data="mi:lang"),
        ],
    ])


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(msg.from_user.id)

    if user and user.get("district_id"):
        # Зарегистрирован — показать меню
        await show_menu(msg, user)
        return

    if user and not user.get("privacy_accepted"):
        await ask_policy(msg, state)
        return

    if user and not user.get("business_name"):
        await msg.answer("Продолжим регистрацию. Введите ваше имя:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(Reg.name)
        return

    # Новый пользователь
    await db.upsert_user(
        msg.from_user.id,
        username=msg.from_user.username,
        first_name=msg.from_user.first_name,
        last_name=msg.from_user.last_name,
    )
    await ask_policy(msg, state)


async def ask_policy(msg: Message, state: FSMContext):
    policy_text = await db.get_privacy_policy("ru")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принимаю", callback_data="policy_accept"),
            InlineKeyboardButton(text="❌ Отказываюсь", callback_data="policy_decline"),
        ]
    ])
    await msg.answer(
        f"📋 *Политика конфиденциальности*\n\n{policy_text}\n\nДля использования сервиса необходимо принять политику.",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await state.set_state(Reg.policy)


@router.callback_query(F.data == "policy_accept", StateFilter(Reg.policy))
async def policy_accepted(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup(reply_markup=None)
    await db.upsert_user(call.from_user.id, privacy_accepted=1)
    await call.message.answer("Отлично! Теперь давайте заполним ваш профиль.\n\n👤 Введите ваше имя:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.name)
    await call.answer()


@router.callback_query(F.data == "policy_decline")
async def policy_declined(call: CallbackQuery, state: FSMContext):
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("❌ Без согласия с политикой конфиденциальности использование сервиса невозможно.\n\nНажмите /start чтобы попробовать снова.")
    await state.clear()
    await call.answer()


@router.message(StateFilter(Reg.name))
async def got_name(msg: Message, state: FSMContext):
    name = msg.text.strip() if msg.text else ""
    if len(name) < 2 or len(name) > 100:
        await msg.answer("Пожалуйста, введите корректное имя (2–100 символов):")
        return
    await state.update_data(name=name)
    await msg.answer(f"Отлично, {name}! 🏪\n\nВведите название вашего бизнеса:")
    await state.set_state(Reg.business)


@router.message(StateFilter(Reg.business))
async def got_business(msg: Message, state: FSMContext):
    biz = msg.text.strip() if msg.text else ""
    if len(biz) < 2 or len(biz) > 200:
        await msg.answer("Пожалуйста, введите корректное название (2–200 символов):")
        return
    await state.update_data(business=biz)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await msg.answer("📱 Поделитесь вашим номером телефона:", reply_markup=kb)
    await state.set_state(Reg.phone)


@router.message(StateFilter(Reg.phone), F.contact)
async def got_phone(msg: Message, state: FSMContext):
    if msg.contact.user_id != msg.from_user.id:
        await msg.answer("Пожалуйста, поделитесь своим собственным номером.")
        return
    await state.update_data(phone=msg.contact.phone_number)
    regions = await db.get_regions()
    await msg.answer("📍 Выберите ваш регион:", reply_markup=ReplyKeyboardRemove())
    await msg.answer("👇", reply_markup=regions_kb(regions))
    await state.set_state(Reg.region)


@router.message(StateFilter(Reg.phone))
async def got_phone_text(msg: Message, state: FSMContext):
    await msg.answer("Пожалуйста, нажмите кнопку «📞 Поделиться номером» для передачи контакта.")


@router.callback_query(F.data.startswith("region:"), StateFilter(Reg.region))
async def got_region(call: CallbackQuery, state: FSMContext):
    region_id = int(call.data.split(":")[1])
    regions = await db.get_regions()
    region = next((r for r in regions if r["id"] == region_id), None)
    if not region:
        await call.answer("Ошибка, попробуйте снова")
        return
    await state.update_data(region_id=region_id, region_name=region["name_ru"])
    districts = await db.get_districts(region_id)
    await call.message.edit_text(
        f"📍 Регион: *{region['name_ru']}*\n\nВыберите район:",
        parse_mode="Markdown",
        reply_markup=districts_kb(districts),
    )
    await state.set_state(Reg.district)
    await call.answer()


@router.callback_query(F.data == "back_regions", StateFilter(Reg.district))
async def back_to_regions(call: CallbackQuery, state: FSMContext):
    regions = await db.get_regions()
    await call.message.edit_text("📍 Выберите ваш регион:", reply_markup=regions_kb(regions))
    await state.set_state(Reg.region)
    await call.answer()


@router.callback_query(F.data.startswith("district:"), StateFilter(Reg.district))
async def got_district(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":", 2)
    district_id = int(parts[1])
    district_name = parts[2]
    data = await state.get_data()

    # Сохранить всё
    await db.upsert_user(
        call.from_user.id,
        first_name=data.get("name"),
        business_name=data.get("business"),
        phone=data.get("phone"),
        region_id=data.get("region_id"),
        district_id=district_id,
        privacy_accepted=1,
    )

    await call.message.edit_text(
        f"✅ *Вы успешно зарегистрированы!*\n\n"
        f"Добро пожаловать в Scenti! 🌿\n\n"
        f"👤 {data.get('name')}\n"
        f"🏪 {data.get('business')}\n"
        f"📍 {data.get('region_name')}, {district_name}",
        parse_mode="Markdown",
    )

    user = await db.get_user(call.from_user.id)
    await show_menu_from_callback(call, user)
    await state.clear()
    await call.answer()


async def show_menu(msg: Message, user: dict):
    balance = fmt_balance(user.get("cashback_balance", 0))
    name = user.get("first_name") or msg.from_user.first_name or "Пользователь"
    biz = user.get("business_name", "")

    await msg.answer("🌿 Главное меню Scenti", reply_markup=main_menu_kb())
    await msg.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"💰 Кешбэк: <b>{balance} сум</b>\n"
        f"🏪 {biz}",
        parse_mode="HTML",
        reply_markup=menu_inline_kb(),
    )


async def show_menu_from_callback(call: CallbackQuery, user: dict):
    balance = fmt_balance(user.get("cashback_balance", 0) if user else 0)
    name = (user.get("first_name") if user else None) or call.from_user.first_name or "Пользователь"
    biz = user.get("business_name", "") if user else ""

    await call.message.answer("🌿 Главное меню Scenti", reply_markup=main_menu_kb())
    await call.message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"💰 Кешбэк: <b>{balance} сум</b>\n"
        f"🏪 {biz}",
        parse_mode="HTML",
        reply_markup=menu_inline_kb(),
    )


@router.message(F.text == "🌿 Открыть Scenti")
async def open_webapp(msg: Message):
    is_https = WEBAPP_URL.startswith("https://")
    if is_https:
        await msg.answer("Открываю Scenti... 🌿")
    else:
        await msg.answer(
            f"🌿 Ваш кабинет Scenti:\n{WEBAPP_URL}\n\n_(В продакшне откроется прямо в Telegram)_",
            parse_mode="Markdown",
        )


@router.callback_query(F.data.startswith("mi:"))
async def menu_inline_cb(call: CallbackQuery):
    action = call.data.split(":")[1]
    user = await db.get_user(call.from_user.id)
    balance = fmt_balance(user.get("cashback_balance", 0)) if user else "0"
    is_https = WEBAPP_URL.startswith("https://")

    if action == "balance":
        await call.answer(f"💰 Баланс: {balance} сум", show_alert=True)

    elif action == "pay":
        text = (
            f"💳 <b>Оплата кешбэком</b>\n\n"
            f"Доступно: <b>{balance} сум</b>\n\n"
            f"Откройте Scenti → нажмите «Оплатить кешбэком» → покажите сумму агенту."
        )
        if not is_https:
            text += f"\n\n🔗 {WEBAPP_URL}"
        await call.message.answer(text, parse_mode="HTML", reply_markup=menu_inline_kb())
        await call.answer()

    elif action == "gifts":
        text = (
            f"🎁 <b>Подарки Scenti</b>\n\n"
            f"Ваш баланс: <b>{balance} сум</b>\n\n"
            f"Откройте Scenti → вкладка «Подарки» → оставьте заявку."
        )
        if not is_https:
            text += f"\n\n🔗 {WEBAPP_URL}"
        await call.message.answer(text, parse_mode="HTML", reply_markup=menu_inline_kb())
        await call.answer()

    elif action == "lang":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang:uz"),
            ]
        ])
        await call.message.answer("🌐 Выберите язык:", reply_markup=kb)
        await call.answer()


@router.message(F.text == "💰 Мой баланс")
async def btn_balance(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        await msg.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    balance = fmt_balance(user.get("cashback_balance", 0))
    await msg.answer(
        f"💰 *Ваш кешбэк-баланс*\n\n*{balance} сум*\n\nКешбэк начисляется автоматически — 10% от суммы каждой покупки.",
        parse_mode="Markdown",
    )


@router.message(F.text == "💳 Оплата кешбэком")
async def btn_pay(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        await msg.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    balance = fmt_balance(user.get("cashback_balance", 0))
    is_https = WEBAPP_URL.startswith("https://")
    text = (
        f"💳 *Оплата кешбэком*\n\n"
        f"Доступно: *{balance} сум*\n\n"
        f"Откройте Scenti и нажмите «Оплатить кешбэком» — покажите сумму агенту при покупке."
    )
    if not is_https:
        text += f"\n\n🔗 {WEBAPP_URL}"
    await msg.answer(text, parse_mode="Markdown")


@router.message(F.text == "🎁 Подарки")
async def btn_gifts(msg: Message):
    user = await db.get_user(msg.from_user.id)
    if not user:
        await msg.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    balance = fmt_balance(user.get("cashback_balance", 0))
    is_https = WEBAPP_URL.startswith("https://")
    text = (
        f"🎁 *Подарки Scenti*\n\n"
        f"Ваш баланс: *{balance} сум*\n\n"
        f"Откройте Scenti → вкладка «Подарки» — выберите продукцию и оставьте заявку."
    )
    if not is_https:
        text += f"\n\n🔗 {WEBAPP_URL}"
    await msg.answer(text, parse_mode="Markdown")


@router.message(F.text == "🌐 Язык")
async def btn_lang(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang:uz"),
        ]
    ])
    await msg.answer("🌐 Выберите язык интерфейса:", reply_markup=kb)


@router.callback_query(F.data.startswith("lang:"))
async def set_lang(call: CallbackQuery):
    lang = call.data.split(":")[1]
    await db.upsert_user(call.from_user.id, language=lang)
    name = "Русский 🇷🇺" if lang == "ru" else "O'zbek 🇺🇿"
    await call.message.edit_text(f"✅ Язык изменён: {name}")
    await call.answer()


@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено. Нажмите /start чтобы начать заново.", reply_markup=ReplyKeyboardRemove())


@router.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "ℹ️ *Scenti — программа лояльности*\n\n"
        "🌿 Получайте 10% кешбэка с каждой покупки ароматов\n"
        "🎁 Обменивайте кешбэк на подарки\n\n"
        "Команды:\n"
        "/start — главное меню\n"
        "/help — помощь\n"
        "/cancel — отменить действие",
        parse_mode="Markdown",
    )


async def main():
    await db.get_conn()  # init DB
    log.info("SQLite database initialized: scenti_local.db")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    log.info("Starting Scenti bot (local SQLite mode)...")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
