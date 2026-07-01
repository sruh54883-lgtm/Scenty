"""Точка входа: запуск бота Scenti в режиме long polling."""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject
from aiogram.dispatcher.middlewares.base import BaseMiddleware

import os as _os


def _build_storage():
    """Return RedisStorage if REDIS_URL is set, else MemoryStorage with a warning.

    MemoryStorage loses all in-progress FSM state (registration flows) on every
    Railway redeploy or process restart.  RedisStorage survives restarts and is
    preferred in production.
    """
    redis_url = _os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(redis_url)
            logging.getLogger("scenti.bot").info("FSM storage: Redis (%s)", redis_url.split("@")[-1])
            return storage
        except Exception as _e:
            logging.getLogger("scenti.bot").warning(
                "RedisStorage init failed (%s) — falling back to MemoryStorage", _e
            )
    else:
        logging.getLogger("scenti.bot").warning(
            "REDIS_URL not set — using MemoryStorage. "
            "All in-progress registrations will be lost on restart. "
            "Set REDIS_URL in Railway to fix this."
        )
    return MemoryStorage()

import database as db
import notifications
from config import settings
from handlers import common, menu, start

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scenti.bot")


class ThrottleMiddleware(BaseMiddleware):
    """Per-user token-bucket rate limiter (in-memory).

    Silently drops updates from a user that arrive faster than
    ``rate`` seconds apart, protecting the asyncpg connection pool
    and preventing Telegram flood-control bans.
    """

    def __init__(self, rate: float = 2.0) -> None:
        self._rate = rate
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            uid = user.id
            now = time.monotonic()
            if now - self._last.get(uid, 0.0) < self._rate:
                return  # silently drop — too fast
            self._last[uid] = now
        return await handler(event, data)


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

    dp = Dispatcher(storage=_build_storage())
    # Rate-limit: at most 1 update per 2 s per user (messages + callbacks)
    dp.message.middleware(ThrottleMiddleware(rate=2.0))
    dp.callback_query.middleware(ThrottleMiddleware(rate=2.0))
    # Порядок важен: специфичные роутеры (start, menu) до общего common
    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(common.router)

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s", me.username)
        await bot.delete_webhook(drop_pending_updates=True)
        # Устанавливаем описание бота
        try:
            await bot.set_my_name("Scenti Loyalty")
            await bot.set_my_description(
                "🌿 Scenti — программа лояльности для клиентов.\n\n"
                "✅ 10% кешбэк за каждую покупку\n"
                "🎁 Накапливайте баллы и обменивайте на ценные подарки\n"
                "📊 История покупок и баланс в одном приложении\n\n"
                "Зарегистрируйтесь и начните экономить прямо сейчас!"
            )
            await bot.set_my_short_description("10% кешбэк за каждую покупку в Scenti 🌿")
        except Exception as _e:
            logger.warning("Не удалось обновить описание бота: %s", _e)
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
