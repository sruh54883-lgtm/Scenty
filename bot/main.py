"""Точка входа: запуск бота Scenti в режиме long polling."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
import notifications
from config import settings
from handlers import common, menu, start

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scenti.bot")


async def main() -> None:
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в .env")

    # БД: если недоступна — бот всё равно стартует (хендлеры ответят «недоступно»)
    db_ok = await db.connect()
    if not db_ok:
        logger.warning("БД недоступна — бот работает в ограниченном режиме")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Переиспользуем этот же Bot в модуле уведомлений
    notifications.set_bot(bot)

    dp = Dispatcher(storage=MemoryStorage())
    # Порядок важен: специфичные роутеры (start, menu) до общего common
    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(common.router)

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.disconnect()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по сигналу")
