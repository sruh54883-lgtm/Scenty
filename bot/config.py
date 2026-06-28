"""Конфигурация бота из переменных окружения (.env в корне проекта)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env лежит в корне проекта (на уровень выше каталога bot/)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost:5432/scenti")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "http://localhost:8080")

    # Telegram ID супер-админа (для уведомлений о новых транзакциях)
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or "0")

    CASHBACK_PERCENT: int = int(os.getenv("CASHBACK_PERCENT", "10"))

    @property
    def webapp_is_https(self) -> bool:
        """Telegram WebApp-кнопки требуют HTTPS. В dev (localhost) их не показываем."""
        return self.WEBAPP_URL.lower().startswith("https://")


settings = Settings()
