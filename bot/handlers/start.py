"""Регистрация клиента: /start → язык → политика → имя → бизнес → телефон → регион → район → диффузор."""
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, URLInputFile

import database as db
from handlers.menu import show_main_menu
from keyboards import inline as ikb
from keyboards import reply as rkb
from states import Registration

router = Router(name="start")
logger = logging.getLogger(__name__)

# ── Тексты на двух языках ────────────────────────────────────────
T = {
    "ru": {
        "choose_lang":      "🌿 Добро пожаловать в <b>Scenti!</b>\n\nВыберите язык / Tilni tanlang:",
        "policy_intro":     "📋 Ознакомьтесь с политикой конфиденциальности:\n\n",
        "policy_accepted":  "✅ Согласие принято!\n\nВведите ваше <b>имя</b>:",
        "policy_declined":  "Без согласия использование сервиса невозможно.\n\nЧтобы попробовать снова — отправьте /start.",
        "name_saved":       "✅ Имя сохранено!\n\nВведите название вашего <b>бизнеса</b>:",
        "business_saved":   "✅ Бизнес сохранён!\n\n📱 Введите ваш <b>номер телефона</b>:\n\nПример: <code>+998901234567</code>",
        "phone_saved":      "✅ Телефон принят!\n\nВыберите ваш <b>регион</b>:",
        "region_selected":  "✅ Регион выбран!\n\nВыберите ваш <b>район</b>:",
        "no_districts":     "В этом регионе пока нет районов. Выберите другой регион:",
        "choose_diffuser":  "✅ Район выбран!\n\n📟 Выберите <b>аппарат</b>, которым вы пользуетесь:",
        "registered":       "🎉 <b>Регистрация успешно завершена!</b>\n\nТеперь за каждую покупку вы получаете <b>кешбэк</b>. Накапливайте баллы и обменивайте их на ценные подарки.",
        "share_phone_btn":  "📞 Поделиться номером",
        "err_name":         "Пожалуйста, введите корректное имя (до 100 символов):",
        "err_business":     "Пожалуйста, введите корректное название (до 200 символов):",
        "err_phone":        "❌ Неверный формат. Введите номер в формате <code>+998901234567</code>:",
        "err_phone_taken":  "❌ Этот номер телефона уже зарегистрирован. Введите другой номер:",
        "err_contact":      "Пожалуйста, введите номер в формате +998XXXXXXXXX:",
        "db_down":          "⚠️ Сервис временно недоступен. Попробуйте позже.",
    },
    "uz": {
        "choose_lang":      "🌿 <b>Scenti</b>ga xush kelibsiz!\n\nTilni tanlang / Выберите язык:",
        "policy_intro":     "📋 Maxfiylik siyosatini o'qing:\n\n",
        "policy_accepted":  "✅ Qabul qilindi!\n\n<b>Ismingizni</b> kiriting:",
        "policy_declined":  "Maxfiylik siyosatisiz xizmatdan foydalanib bo'lmaydi.\n\nQayta urinish uchun /start yuboring.",
        "name_saved":       "✅ Ism saqlandi!\n\n<b>Biznesingiz nomini</b> kiriting:",
        "business_saved":   "✅ Biznes saqlandi!\n\n📱 <b>Telefon raqamingizni</b> kiriting:\n\nMisol: <code>+998901234567</code>",
        "phone_saved":      "✅ Telefon qabul qilindi!\n\n<b>Viloyatingizni</b> tanlang:",
        "region_selected":  "✅ Viloyat tanlandi!\n\n<b>Tumaningizni</b> tanlang:",
        "no_districts":     "Bu viloyatda tumanlar yo'q. Boshqa viloyat tanlang:",
        "choose_diffuser":  "✅ Tuman tanlandi!\n\n📟 Foydalanadigan <b>apparatingizni</b> tanlang:",
        "registered":       "🎉 <b>Ro'yxatdan o'tish muvaffaqiyatli yakunlandi!</b>\n\nHar bir xaridingiz uchun <b>keshbek</b> olasiz. Ballaringizni to'plang va qimmatli sovg'alarga almashtiring.",
        "share_phone_btn":  "📞 Raqamni ulashish",
        "err_name":         "Iltimos, to'g'ri ism kiriting (100 belgigacha):",
        "err_business":     "Iltimos, to'g'ri nom kiriting (200 belgigacha):",
        "err_phone":        "❌ Noto'g'ri format. <code>+998901234567</code> shaklida kiriting:",
        "err_phone_taken":  "❌ Bu raqam allaqachon ro'yxatdan o'tgan. Boshqa raqam kiriting:",
        "err_contact":      "Iltimos, raqamni +998XXXXXXXXX formatida kiriting:",
        "db_down":          "⚠️ Xizmat vaqtincha mavjud emas. Keyinroq urinib ko'ring.",
    },
}


def t(state_data: dict, key: str) -> str:
    lang = state_data.get("lang", "ru")
    return T.get(lang, T["ru"]).get(key, key)


def t_lang(lang: str, key: str) -> str:
    return T.get(lang, T["ru"]).get(key, key)


MAX_NAME_LEN = 100
MAX_BUSINESS_LEN = 200


# ── /start ───────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await state.clear()

    if not db.is_available():
        await message.answer(T["ru"]["db_down"])
        return

    telegram_id = message.from_user.id
    try:
        user = await db.get_user_by_telegram_id(telegram_id)
    except Exception:
        logger.exception("Ошибка чтения пользователя в /start")
        await message.answer(T["ru"]["db_down"])
        return

    if user and user.get("is_active") and user.get("privacy_accepted") and user.get("district_id"):
        await show_main_menu(message, user)
        return

    # Первый шаг — выбор языка
    await state.set_state(Registration.waiting_language)
    await message.answer(T["ru"]["choose_lang"], reply_markup=ikb.language_keyboard())


# ── 0. Выбор языка ───────────────────────────────────────────────

@router.callback_query(Registration.waiting_language, F.data.startswith(ikb.CB_LANG_PREFIX))
async def process_language(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    lang = callback.data.split(":", 1)[1]  # "ru" или "uz"
    if lang not in ("ru", "uz"):
        lang = "ru"
    await state.update_data(lang=lang)
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_policy(callback.message, state)


# ── 1. Политика ──────────────────────────────────────────────────

async def _ask_policy(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    try:
        policy_data = await db.get_privacy_policy(lang)
    except Exception:
        logger.exception("Ошибка загрузки политики")
        await message.answer(t_lang(lang, "db_down"))
        return

    # Отправить документ политики
    tg_file_id = policy_data.get("tg_file_id", "")
    file_url = policy_data.get("file_url", "")
    if tg_file_id:
        # Используем постоянный Telegram file_id — не зависит от Railway filesystem
        try:
            await message.answer_document(tg_file_id)
        except Exception as _e:
            logger.warning("Не удалось отправить документ по tg_file_id: %s", _e)
    elif file_url:
        try:
            from config import settings
            from urllib.parse import urlparse
            parsed = urlparse(settings.WEBAPP_URL)
            base = f"{parsed.scheme}://{parsed.netloc}"
            full_url = base + file_url if file_url.startswith("/") else file_url
            doc = URLInputFile(full_url, filename=file_url.split("/")[-1])
            await message.answer_document(doc)
        except Exception as _e:
            logger.warning("Не удалось отправить PDF по URL: %s", _e)

    has_file = bool(policy_data.get("tg_file_id") or policy_data.get("file_url"))
    content = policy_data["text"]
    # Не показываем заглушку "временно недоступна" если файл уже отправлен
    fallbacks = {
        "Политика конфиденциальности временно недоступна.",
        "Maxfiylik siyosati vaqtincha mavjud emas.",
    }
    if has_file and content in fallbacks:
        content = ""
    body = t_lang(lang, "policy_intro")
    if content:
        body += content
    text = "🌿 <b>Scenti</b>\n\n" + body
    await state.set_state(Registration.waiting_policy)
    await message.answer(text, reply_markup=ikb.policy_keyboard(lang))


@router.callback_query(Registration.waiting_policy, F.data == ikb.CB_POLICY_ACCEPT)
async def policy_accept(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Registration.waiting_name)
    await callback.message.answer(t(data, "policy_accepted"), reply_markup=rkb.remove())


@router.callback_query(Registration.waiting_policy, F.data == ikb.CB_POLICY_DECLINE)
async def policy_decline(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.clear()
    await callback.message.answer(t(data, "policy_declined"))


# ── 2. Имя ───────────────────────────────────────────────────────

@router.message(Registration.waiting_name, F.text)
async def process_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    name = (message.text or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        await message.answer(t(data, "err_name"))
        return
    await state.update_data(name=name)
    await state.set_state(Registration.waiting_business)
    await message.answer(t(data, "name_saved"))


# ── 3. Бизнес ────────────────────────────────────────────────────

@router.message(Registration.waiting_business, F.text)
async def process_business(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    business = (message.text or "").strip()
    if not business or len(business) > MAX_BUSINESS_LEN:
        await message.answer(t(data, "err_business"))
        return
    await state.update_data(business_name=business)
    await state.set_state(Registration.waiting_phone)
    await message.answer(t(data, "business_saved"), reply_markup=rkb.remove())


# ── 4. Телефон (ручной ввод) ─────────────────────────────────────

import re as _re

_PHONE_RE = _re.compile(r"^\+998\d{9}$")


def _normalize_phone(raw: str) -> str:
    digits = _re.sub(r"[^\d+]", "", raw)
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


@router.message(Registration.waiting_phone, F.text)
async def process_phone_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = (message.text or "").strip()
    if not _PHONE_RE.match(raw):
        await message.answer(t(data, "err_phone"))
        return
    phone = _normalize_phone(raw)
    try:
        existing = await db.get_user_by_phone(phone)
    except Exception:
        existing = None
    if existing:
        await message.answer(t(data, "err_phone_taken"))
        return
    await state.update_data(phone=phone)
    await _ask_region(message, state)


@router.message(Registration.waiting_phone)
async def process_phone_invalid(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await message.answer(t(data, "err_phone"))


async def _ask_region(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    try:
        regions = await db.get_regions()
    except Exception:
        logger.exception("Ошибка загрузки регионов")
        await message.answer(t(data, "db_down"))
        return
    if not regions:
        await message.answer(t(data, "db_down"))
        return
    await state.set_state(Registration.waiting_region)
    await message.answer(
        t(data, "phone_saved"),
        reply_markup=ikb.regions_keyboard(regions, lang),
    )


# ── 5. Регион ────────────────────────────────────────────────────

@router.callback_query(Registration.waiting_region, F.data.startswith(ikb.CB_REGION_PREFIX))
async def process_region(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    lang = data.get("lang", "ru")
    try:
        region_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Error. Try /start")
        return

    try:
        districts = await db.get_districts_by_region(region_id)
    except Exception:
        logger.exception("Ошибка загрузки районов")
        await callback.message.answer(t(data, "db_down"))
        return

    await state.update_data(region_id=region_id)

    if not districts:
        await callback.message.edit_text(t(data, "no_districts"))
        regions = await db.get_regions()
        await callback.message.edit_reply_markup(
            reply_markup=ikb.regions_keyboard(regions, lang)
        )
        return

    await state.set_state(Registration.waiting_district)
    await callback.message.edit_text(t(data, "region_selected"))
    await callback.message.edit_reply_markup(
        reply_markup=ikb.districts_keyboard(districts, lang)
    )


# ── 6. Район ─────────────────────────────────────────────────────

@router.callback_query(Registration.waiting_district, F.data == ikb.CB_DISTRICT_BACK)
async def district_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    lang = data.get("lang", "ru")
    regions = await db.get_regions()
    await state.set_state(Registration.waiting_region)
    await callback.message.edit_text(t(data, "phone_saved"))
    await callback.message.edit_reply_markup(reply_markup=ikb.regions_keyboard(regions, lang))


@router.callback_query(Registration.waiting_district, F.data.startswith(ikb.CB_DISTRICT_PREFIX))
async def process_district(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    lang = data.get("lang", "ru")
    try:
        district_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Error. Try /start")
        return

    await state.update_data(district_id=district_id)

    diffusers = await db.get_diffusers()
    if not diffusers:
        # Если диффузоров нет — завершаем без выбора
        await _complete_registration(callback, state, district_id, None, lang)
        return

    await state.set_state(Registration.waiting_diffuser)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        t_lang(lang, "choose_diffuser"),
        reply_markup=ikb.diffusers_keyboard(diffusers, lang),
    )


@router.callback_query(Registration.waiting_diffuser, F.data.startswith(ikb.CB_DIFFUSER_PREFIX))
async def process_diffuser(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    lang = data.get("lang", "ru")
    try:
        diffuser_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.message.answer("Error. Try /start")
        return

    district_id = data.get("district_id")
    if not district_id:
        await callback.message.answer("Что-то пошло не так. Попробуйте /start")
        await state.clear()
        return

    await _complete_registration(callback, state, int(district_id), diffuser_id, lang)


async def _complete_registration(
    callback: CallbackQuery,
    state: FSMContext,
    district_id: int,
    diffuser_id,
    lang: str,
) -> None:
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
            diffuser_id=diffuser_id,
            language=lang,
        )
    except KeyError:
        logger.warning("Неполные данные FSM при сохранении пользователя")
        await callback.message.answer("Что-то пошло не так. Попробуйте /start")
        await state.clear()
        return
    except Exception:
        logger.exception("Ошибка сохранения пользователя")
        await callback.message.answer(T.get(lang, T["ru"]).get("db_down", ""))
        return

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        t_lang(lang, "registered"),
        reply_markup=ikb.webapp_keyboard(lang),
    )
    await show_main_menu(callback.message, user)
