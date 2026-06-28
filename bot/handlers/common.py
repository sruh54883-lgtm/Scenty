"""Общие команды: /help, /cancel и фоллбэк-обработчик."""
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from keyboards import reply as rkb

router = Router(name="common")
logger = logging.getLogger(__name__)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🌿 <b>Scenti — программа лояльности</b>\n\n"
        "Доступные команды:\n"
        "/start — регистрация или открыть меню\n"
        "/cancel — отменить текущее действие\n"
        "/help — помощь\n\n"
        "По вопросам обращайтесь к вашему агенту Scenti."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять. Отправьте /start.", reply_markup=rkb.remove())
        return
    await state.clear()
    await message.answer(
        "Действие отменено. Отправьте /start, чтобы начать заново.",
        reply_markup=rkb.remove(),
    )
